"""Резервные тексты привязаны к официальной редакции и не дают категорию."""

from app.services.published_copies import PublishedCopy, merge_copies, read_copies
from app.services.official_reference import OfficialDocument, OfficialIndex, OfficialPage
from tests.backend.test_official_reference import make_reference, SOURCE


def test_copy_requires_exact_edition_and_uses_only_original_treatment(tmp_path):
    html = """
    <h1>Тестовый диагноз — КР494</h1>
    <a class="krv2-pdf-link" href="https://cr.minzdrav.gov.ru/view-cr/494_3">Карточка</a>
    <a class="krv2-pdf-link" href="https://apicr.minzdrav.gov.ru/api.ashx?op=GetClinrecPdf&amp;id=494_3">PDF</a>
    <details id="sec-treatment">
      <details class="krv2-sub" data-num="3.1">
        <div class="krv2-rec-summary">Сгенерированный пересказ</div>
        <div class="krv2-rec-original">Проверенный оригинальный фрагмент.</div>
        <p id="b-treatment-1-1">Абзац раздела лечения.</p>
      </details>
    </details>
    <div class="krv2-rec-original">Текст из другого раздела.</div>
    """
    (tmp_path / "K25.html").write_text(html, encoding="utf-8")
    assert not read_copies(tmp_path, {"494_4": "Тестовый диагноз"})
    assert not read_copies(tmp_path, {"494_3": "Другой диагноз"})
    copy = read_copies(tmp_path, {"494_3": "Тестовый диагноз"})["494_3"]
    assert copy.url == "https://clinreky.ru/nosology/K25/"
    assert copy.segments == [("Раздел 3.1", "Проверенный оригинальный фрагмент."),
                             ("Раздел 3.1", "Абзац раздела лечения.")]


def test_mentions_from_copy_are_marked_and_never_classify():
    base = make_reference()
    source = SOURCE.model_copy(update={"url": "https://apicr.minzdrav.gov.ru/api.ashx?op=GetClinrecPdf&id=494_3"})
    document = OfficialDocument(code="K25", diagnosis="Другой диагноз", scope="group",
                                source=source, state="copy_indexed")
    copy = "https://clinreky.ru/nosology/K25/"
    page = OfficialPage(url=source.url, number=5, text="Изучали компонент б и компонент а в группе.",
                        location="Раздел 3.1", copy_url=copy)
    snapshot = base.model_copy(update={"documents": (document,), "pages": (page,)})
    index = OfficialIndex(snapshot)
    drug = index.resolve("Независимый тестовый препарат")
    result = index.lookup(drug, "K25.9")
    assert result["reason_code"] == "OFFICIAL_COPY_MENTION"
    assert result["review_sources"][0]["origin"] == "published_copy"
    assert result["review_sources"][0]["copy_url"] == copy
    assert result["review_sources"][0]["url"] == source.url
    assert not result["relationship_found"] and result.get("category") is None

    page_without_drug = page.model_copy(update={"text": "Не упоминается какой-либо компонент."})
    not_found = OfficialIndex(snapshot.model_copy(update={"pages": (page_without_drug,)})).lookup(drug, "K25.9")
    assert not_found["reason_code"] == "OFFICIAL_COPY_LINK_NOT_FOUND"
    assert not_found["searched_sources"][0]["copy_url"] == copy
    assert not not_found["review_sources"]


def test_merge_adds_copy_only_when_original_text_is_unavailable():
    base = make_reference()
    source = SOURCE.model_copy(update={
        "url": "https://apicr.minzdrav.gov.ru/api.ashx?op=GetClinrecPdf&id=494_3",
        "version": "494_3 · 2026-11-01",
    })
    original = OfficialPage(url=source.url, number=1, text="Оригинал.")
    document = OfficialDocument(code="K25", diagnosis="Другой диагноз", scope="group",
                                source=source, state="indexed")
    copy_url = "https://clinreky.ru/nosology/K25/"
    copies = {"494_3": PublishedCopy(copy_url, [("Раздел 3", "Копия.")])}
    with_original = base.model_copy(update={"documents": (document,), "pages": (original,)})
    assert merge_copies(with_original, copies) is with_original
    no_original = base.model_copy(update={"documents": (document.model_copy(update={"state": "pending"}),)})
    merged = merge_copies(no_original, copies)
    assert merged.documents[0].state == "copy_indexed"
    assert merged.pages[0].copy_url == copy_url
    assert merged.evidence == no_original.evidence and merged.rules == no_original.rules
    assert merge_copies(merged, copies).version == merged.version
