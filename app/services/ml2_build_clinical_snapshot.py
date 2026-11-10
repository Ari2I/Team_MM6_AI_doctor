"""Собрать отдельный снимок ГРЛС и доступных копий КР для заданных кодов МКБ."""
import argparse
import re
from pathlib import Path
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx

from app.services.ml2_clinicalrecs_source import fetch_recommendation, recommendations_for_diagnosis
from app.services.official_catalog import PDF_URL, _write
from app.services.official_reference import OfficialDocument, OfficialSnapshot, Source
from app.services.published_copies import AGENT, BASE, merge_copies, read_copies


def build(registry: Path, output: Path, codes: list[str]) -> None:
    if registry.resolve() == output.resolve():
        raise ValueError("Укажите отдельный выходной файл.")
    snapshot = OfficialSnapshot.model_validate_json(registry.read_bytes())
    if snapshot.documents or snapshot.rules or snapshot.pages:
        raise ValueError("На вход нужен снимок с реестром без клинического корпуса.")
    documents = []
    titles = {}
    for code in codes:
        candidates = recommendations_for_diagnosis(code)
        if not candidates:
            raise ValueError(f"{code}: в каталоге нет подходящих редакций.")
        for candidate in candidates:
            if candidate.edition_id in titles:
                continue
            cached = fetch_recommendation(candidate.page_url)
            rec = cached.recommendation
            if (candidate.cache_state == "STALE_CACHE" or cached.cache_state == "STALE_FALLBACK"
                    or not rec.is_current_ministry_copy or rec.edition_id != candidate.edition_id
                    or rec.primary_url != candidate.primary_url or rec.title != candidate.title
                    or not {code, code.split(".")[0]}.intersection(rec.icd10)):
                raise ValueError(f"{candidate.edition_id}: не подтверждён паспорт редакции.")
            titles[rec.edition_id] = rec.title
            source = Source(title=rec.title, url=f"{PDF_URL}&id={rec.edition_id}",
                            version=f"{rec.edition_id} · {rec.year}", location="Паспорт КР",
                            retrieved_at=rec.retrieved_at[:10], quote=rec.title)
            for icd in rec.icd10:
                documents.append(OfficialDocument(code=icd, diagnosis=rec.title,
                                 state="pending", scope="exact" if "." in icd else "group", source=source))
    cache = registry.parent.parent / "ml2-mirrors"
    cache.mkdir(parents=True, exist_ok=True)
    parents = {code.split(".")[0] for code in codes}
    with httpx.Client(headers={"User-Agent": AGENT}, timeout=20) as client:
        robots = RobotFileParser()
        robots.parse(client.get(BASE + "/robots.txt").raise_for_status().text.splitlines())
        sitemap = ElementTree.fromstring(client.get(BASE + "/sitemap.xml").raise_for_status().content)
        urls = sorted({node.text.strip() for node in sitemap.iter()
                       if node.tag.split("}")[-1] == "loc" and node.text})
        for url in urls:
            match = re.fullmatch(re.escape(BASE) + r"/nosology/([A-Za-z0-9._-]+)/", url)
            if not match or match[1][:3] not in parents or not robots.can_fetch(AGENT, url):
                continue
            response = client.get(url).raise_for_status()
            if len(response.content) <= 10_000_000 and "html" in response.headers.get("content-type", ""):
                _write(cache / (match[1] + ".html"), response.content)
    copies = read_copies(cache, titles)
    missing = titles.keys() - copies.keys()
    if missing:
        raise ValueError("Нет проверенной текстовой копии для редакций " + ", ".join(sorted(missing)))
    combined = merge_copies(snapshot.model_copy(update={"documents": tuple(documents), "coverage": "limited"}), copies)
    data = combined.model_dump_json().encode()
    OfficialSnapshot.model_validate_json(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write(output, data)
    print(f"{combined.version}: registrations={len(combined.registrations)}, editions={len(copies)}, sections={len(combined.pages)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("codes", nargs="+")
    args = parser.parse_args()
    build(args.registry, args.output, args.codes)
