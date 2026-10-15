#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seleciona até 1.000 vagas nacionais de estágio, jovem aprendiz e primeiro emprego."""

import gzip
import hashlib
import io
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

FEED_URL = "https://feeds.whatjobs.com/sinerj/sinerj_pt_BR.xml.gz"
OUTPUT_FOLDER = Path("json_parts")
MAX_JOBS = 1000
MAX_SALARY_BRL = 10000.0
REQUEST_TIMEOUT = 90

# Termos são avaliados sem acentos, em título, tipo de vaga e descrição.
TARGET_RE = re.compile(
    r"\b(?:"
    r"estagio|estagiari[oa]s?|"
    r"jovens?\s+aprendiz(?:es)?|aprendiz(?:es)?|"
    r"programa\s+de\s+aprendizagem|aprendizagem\s+profissional|"
    r"primeiro\s+emprego|primeira\s+experiencia|primeira\s+oportunidade"
    r")\b",
    re.IGNORECASE,
)

MONEY_NUMBER = (
    r"(?:"
    r"\d{1,3}(?:\.\d{3})+(?:,\d{2})?"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d{2})?"
    r"|\d+(?:,\d{2}|\.\d{2})?"
    r")"
    r"(?:\s*(?:mil|k)\b)?"
)
SALARY_AMOUNT_RE = re.compile(
    rf"\b(?:faixa salarial|piso salarial|sal[aá]rios?|remunera[cç][aã]o|proventos)\b"
    rf"(?:\s+(?:fixo|base|mensal(?:\s+de)?|bruto|inicial|m[ií]nimo garantido|"
    rf"oferecido|oferecida|oferecidos|oferecidas|de at[eé]|a partir de|entre)){{0,3}}"
    rf"\s*(?:[:：=–—-]\s*)?"
    rf"(?:(?:a partir de|de|entre)\s+)?"
    rf"(?P<amount>(?:R\$\s*)?{MONEY_NUMBER})(?![\d.,])",
    re.IGNORECASE,
)
SALARY_VALUE_RE = re.compile(
    rf"(?<![\w])(?:R\$\s*|[$€£]\s*)?{MONEY_NUMBER}(?![\d.,])",
    re.IGNORECASE,
)


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().lower()


def clean_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def text_at(element, path):
    return (element.findtext(path) or "").strip()


def has_real_salary(salary):
    value = normalize(salary)
    if not value:
        return False
    placeholders = (
        "a combinar", "nao informado", "nao informada",
        "nao divulgado", "nao divulgada", "confidencial",
        "negociavel", "sob consulta", "salario nao informado",
        "salario a combinar",
    )
    if any(marker in value for marker in placeholders):
        return False
    if not re.search(r"\d", value):
        return False
    if not re.search(r"(?:R\$|[$€£])", value) and re.fullmatch(r"\d+", value):
        return False
    return True


def parse_salary_amount(raw_amount):
    """Interpreta formato brasileiro/internacional e sufixos mil/k."""
    raw_value = str(raw_amount or "").strip()
    multiplier = 1000.0 if re.search(r"\s*(?:mil|k)\b", raw_value, re.IGNORECASE) else 1.0
    value = re.sub(r"\s*(?:mil|k)\b", "", raw_value, flags=re.IGNORECASE)
    value = re.sub(r"^(?:R\$|[$€£])\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[^\d.,]", "", value)
    if not value:
        return None

    if "." in value and "," in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        left, _, right = value.rpartition(",")
        if len(right) == 3 and left:
            value = value.replace(",", "")
        else:
            value = left.replace(",", "") + "." + right
    elif "." in value:
        parts = value.split(".")
        if len(parts) > 1 and len(parts[0]) <= 3 and all(len(part) == 3 for part in parts[1:]):
            value = "".join(parts)
        else:
            value = "".join(parts[:-1]) + "." + parts[-1]

    try:
        return float(value) * multiplier
    except ValueError:
        return None


def salary_exceeds_limit(salary, description=""):
    """Descarta salário ou teto de faixa salarial acima de R$ 10.000."""
    for match in SALARY_VALUE_RE.finditer(salary or ""):
        amount = parse_salary_amount(match.group(0))
        if amount is not None and amount > MAX_SALARY_BRL:
            return True

    text = clean_html(description)
    range_tail_re = re.compile(
        rf"\s*(?:-|–|—|\ba\b|\bat[eé]\b)\s*(?:R\$\s*)?({MONEY_NUMBER})(?![\d.,])",
        re.IGNORECASE,
    )
    for match in SALARY_AMOUNT_RE.finditer(text):
        amount = parse_salary_amount(match.group("amount"))
        if amount is not None and amount > MAX_SALARY_BRL:
            return True
        range_match = range_tail_re.match(text[match.end():match.end() + 100])
        if range_match:
            upper_amount = parse_salary_amount(range_match.group(1))
            if upper_amount is not None and upper_amount > MAX_SALARY_BRL:
                return True
    return False


def extract_salary(structured_salary, description):
    structured_salary = re.sub(r"\s+", " ", structured_salary or "").strip()
    if has_real_salary(structured_salary):
        return structured_salary

    text = clean_html(description)
    for match in SALARY_AMOUNT_RE.finditer(text):
        amount = re.sub(r"\s+", " ", match.group("amount")).strip()
        raw_number = re.sub(r"^R\$\s*", "", amount, flags=re.IGNORECASE).strip()
        if not amount.upper().startswith("R$") and re.fullmatch(r"\d{1,2}", raw_number):
            continue
        return amount
    return ""


def parse_date(raw):
    if not raw:
        return None
    value = raw.strip()
    for fmt in (
        "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def build_job(elem):
    title = text_at(elem, "title")
    description_raw = text_at(elem, "description")
    company = text_at(elem, "company/name") or "Confidencial"
    url = text_at(elem, "urlDeeplink") or text_at(elem, "link")
    job_type = text_at(elem, "jobType")
    structured_salary = text_at(elem, "salary")

    location = elem.find("locations/location")
    city = text_at(location, "city") if location is not None else text_at(elem, "city")
    state = text_at(location, "state") if location is not None else text_at(elem, "state")

    pub_raw = text_at(elem, "pubdate") or text_at(elem, "pubDate")
    updated_raw = text_at(elem, "updated")
    pub_date = parse_date(pub_raw) or parse_date(updated_raw)

    if not title or not url or not city or not state:
        return None

    searchable_text = normalize(" ".join((title, job_type)))
    if not TARGET_RE.search(searchable_text):
        return None

    salary = extract_salary(structured_salary, description_raw)
    if not salary or salary_exceeds_limit(salary, description_raw):
        return None

    published = pub_date.isoformat() if pub_date else ""
    url_key = url.strip().rstrip("/")
    unique_id = hashlib.sha256(
        f"{normalize(title)}|{normalize(company)}|{normalize(city)}|{url_key}".encode("utf-8")
    ).hexdigest()

    return {
        "id": unique_id,
        "title": title,
        "description": clean_html(description_raw),
        "company": company,
        "salary": salary,
        "city": city,
        "state": state,
        "tipo": job_type or "Não informado",
        "url": url,
        "data_publicacao": published,
        "origem": "WhatJobs",
    }, url_key, pub_date


def main():
    print("Baixando feed nacional do WhatJobs...")
    try:
        response = requests.get(
            FEED_URL,
            headers={"User-Agent": "JovemAprendizBR-FeedProcessor/1.0"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SystemExit(f"ERRO ao baixar o feed: {exc}")

    candidates = {}
    counts = {
        "xml_lidas": 0,
        "fora_das_categorias": 0,
        "sem_salario_valido": 0,
        "salario_acima_de_10000": 0,
        "duplicadas": 0,
        "dados_obrigatorios_ausentes": 0,
    }

    try:
        with gzip.open(io.BytesIO(response.content), "rt", encoding="utf-8") as feed:
            for _, elem in ET.iterparse(feed, events=("end",)):
                if elem.tag.split("}")[-1] != "job":
                    continue

                counts["xml_lidas"] += 1
                built = build_job(elem)
                if built is None:
                    title = text_at(elem, "title")
                    description = text_at(elem, "description")
                    job_type = text_at(elem, "jobType")
                    if not title or not (text_at(elem, "urlDeeplink") or text_at(elem, "link")):
                        counts["dados_obrigatorios_ausentes"] += 1
                    elif not TARGET_RE.search(normalize(" ".join((title, job_type)))):
                        counts["fora_das_categorias"] += 1
                    else:
                        salary = extract_salary(text_at(elem, "salary"), description)
                        if not salary:
                            counts["sem_salario_valido"] += 1
                        else:
                            counts["salario_acima_de_10000"] += 1
                    elem.clear()
                    continue

                job, url_key, pub_date = built
                if url_key in candidates:
                    counts["duplicadas"] += 1
                    elem.clear()
                    continue
                candidates[url_key] = (job, pub_date)
                elem.clear()
    except (OSError, EOFError, ET.ParseError) as exc:
        raise SystemExit(f"ERRO ao interpretar o XML compactado: {exc}")

    ordered = sorted(
        candidates.values(),
        key=lambda item: item[1] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    jobs = [job for job, _ in ordered[:MAX_JOBS]]

    if not jobs:
        raise SystemExit("ERRO: não há vagas válidas; os arquivos existentes foram preservados.")

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    # Só remove partes antigas depois de obter um resultado novo e não vazio.
    for old_part in OUTPUT_FOLDER.glob("part_*.json"):
        old_part.unlink()

    output = OUTPUT_FOLDER / "part_1.json"
    temp = output.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(jobs, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp.replace(output)

    print("Resumo:")
    for name, value in counts.items():
        print(f"  {name}: {value}")
    print(f"  vagas selecionadas: {len(jobs)}")
    print(f"Arquivo gerado: {output}")


if __name__ == "__main__":
    main()
