#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import gzip
import hashlib
import io
import json
import os
import random
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

FEED_URL = "https://feeds.whatjobs.com/sinerj/sinerj_pt_BR.xml.gz"
OUTPUT_FOLDER = "json_parts"
STATE_FILE = "daily_jobs_state.json"
MAX_JOBS_PER_DAY = 100
TIMEZONE = ZoneInfo("America/Sao_Paulo")

CIDADES = None

def normalize(text):
    return (text or "").strip().lower()

def clean_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()

def parse_date(value):
    if not value:
        return None
    value = value.strip()
    formats = (
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

def job_key(job):
    return job.get("url") or hashlib.md5(
        f"{job.get('title','')}|{job.get('company','')}|{job.get('city','')}".encode("utf-8")
    ).hexdigest()

def load_state(today):
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") != today:
            return {}
        return state.get("jobs", {})
    except (OSError, json.JSONDecodeError):
        return {}

def save_state(today, selected):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": today, "jobs": selected}, f, ensure_ascii=False, indent=2)

def gerar_intro(titulo, cidade):
    intros = [
        f"Confira a oportunidade para {titulo} em {cidade}. Veja requisitos e como se candidatar.",
        f"Nova vaga disponível para {titulo} em {cidade}. Saiba mais sobre essa oportunidade.",
        f"Empresa está contratando {titulo} em {cidade}. Veja detalhes e envie seu currículo.",
    ]
    return random.choice(intros)

def build_job(elem):
    title = elem.findtext("title", "").strip()
    description = elem.findtext("description", "").strip()
    company = elem.findtext("company/name", "").strip() or "Confidencial"
    url = elem.findtext("urlDeeplink", "").strip() or elem.findtext("link", "").strip()
    job_type = elem.findtext("jobType", "").strip()
    location_elem = elem.find("locations/location")
    city = location_elem.findtext("city", "").strip() if location_elem is not None else elem.findtext("city", "").strip()
    state = location_elem.findtext("state", "").strip() if location_elem is not None else elem.findtext("state", "").strip()

    pub_raw = elem.findtext("pubdate", "").strip() or elem.findtext("pubDate", "").strip()
    updated_raw = elem.findtext("updated", "").strip()
    pub_dt = parse_date(pub_raw)
    updated_dt = parse_date(updated_raw)

    if not title or not url or not city or not state or not pub_dt:
        return None

    today = datetime.now(TIMEZONE).date()
    if pub_dt.date() != today:
        return None

    if not job_matches(city, title, description):
        return None

    description = clean_html(description)
    if False:
        description = gerar_intro(title, city) + "\n\n" + description

    return {
        "id": hashlib.md5(f"{title}-{company}-{city}-{url}".encode("utf-8")).hexdigest(),
        "title": title,
        "description": description,
        "company": company,
        "city": city,
        "state": state,
        "url": url,
        "tipo": job_type,
        "data_publicacao": pub_dt.date().isoformat(),
        "data_atualizacao": updated_dt.date().isoformat() if updated_dt else "",
        "origem": "WhatJobs",
    }

def job_matches(city, title, description):
    return "aprendiz" in normalize(title)

def main():
    today = datetime.now(TIMEZONE).date().isoformat()
    print(f"📅 Data de referência: {today}")

    selected = load_state(today)

    print("📥 Baixando feed do WhatJobs...")
    try:
        response = requests.get(
            FEED_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FeedProcessor/1.0)"},
            timeout=60,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        raise SystemExit(f"❌ Erro ao baixar o feed: {e}")

    candidates = {}

    with gzip.open(io.BytesIO(response.content), "rt", encoding="utf-8") as f:
        for event, elem in ET.iterparse(f, events=("end",)):
            if elem.tag != "job":
                continue
            job = build_job(elem)
            if job:
                key = job_key(job)
                candidates[key] = job
            elem.clear()

    # Atualiza o conteúdo dos jobs que já foram selecionados hoje.
    for key in list(selected):
        if key in candidates:
            selected[key] = candidates[key]

    # Completa a cota diária com as vagas mais novas do dia.
    novos = [
        job for key, job in candidates.items()
        if key not in selected
    ]
    novos.sort(key=lambda j: (j.get("data_publicacao", ""), j.get("data_atualizacao", "")), reverse=True)

    for job in novos:
        if len(selected) >= MAX_JOBS_PER_DAY:
            break
        selected[job_key(job)] = job

    jobs = list(selected.values())[:MAX_JOBS_PER_DAY]

    # Um único JSON por execução: no máximo 100 vagas novas por dia,
    # mantendo as mesmas vagas para atualização a cada 6 horas.
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    for filename in os.listdir(OUTPUT_FOLDER):
        if filename.endswith(".json"):
            os.remove(os.path.join(OUTPUT_FOLDER, filename))

    output_path = os.path.join(OUTPUT_FOLDER, "part_1.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)

    save_state(today, selected)

    print(f"✅ Vagas encontradas hoje: {len(candidates)}")
    print(f"✅ Vagas selecionadas na cota do dia: {len(jobs)}")
    print(f"📄 Gerado: {output_path}")

if __name__ == "__main__":
    main()
