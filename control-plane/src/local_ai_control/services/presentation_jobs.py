"""Private presentation job workspace, durable state, and safe PPTX parsing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET


JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{3,63}$")
PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


class PresentationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedSlide:
    number: int
    title: str
    body: tuple[str, ...]
    other_text: tuple[str, ...]
    notes: str
    chart_text: tuple[str, ...]
    alt_text: tuple[str, ...]

    @property
    def source_text(self) -> str:
        values = [self.title, *self.body, *self.other_text, *self.chart_text]
        return "\n".join(value for value in values if value).strip()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> str:
    value = PurePosixPath(name)
    if value.is_absolute() or ".." in value.parts:
        raise PresentationError("PPTX_MEMBER_PATH_INVALID")
    return value.as_posix()


def _resolve_zip_target(source: str, target: str) -> str:
    base = PurePosixPath(source).parent
    parts: list[str] = []
    for part in (base / target).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise PresentationError("PPTX_RELATIONSHIP_ESCAPE")
            parts.pop()
        else:
            parts.append(part)
    return _safe_member("/".join(parts))


def _relationships(archive: zipfile.ZipFile, source: str) -> dict[str, tuple[str, str]]:
    source_path = PurePosixPath(source)
    rel_path = source_path.parent / "_rels" / f"{source_path.name}.rels"
    try:
        root = ET.fromstring(archive.read(_safe_member(rel_path.as_posix())))
    except KeyError:
        return {}
    except ET.ParseError as exc:
        raise PresentationError("PPTX_XML_INVALID") from exc
    values = {}
    for rel in root.findall(f"{{{PKG_REL}}}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        rel_id, target, kind = rel.get("Id"), rel.get("Target"), rel.get("Type")
        if rel_id and target and kind:
            values[rel_id] = (_resolve_zip_target(source, target), kind)
    return values


def _text_values(xml: bytes) -> list[str]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise PresentationError("PPTX_XML_INVALID") from exc
    return [node.text.strip() for node in root.iter(f"{{{DRAWING}}}t") if node.text and node.text.strip()]


class PPTXParser:
    def __init__(self, max_bytes: int = 100 * 1024 * 1024):
        self.max_bytes = max_bytes

    def parse(self, input_path: Path | str) -> list[ParsedSlide]:
        path = Path(input_path)
        if path.suffix.lower() != ".pptx":
            raise PresentationError("PPTX_ONLY")
        if path.is_symlink() or not path.is_file():
            raise PresentationError("PPTX_SOURCE_INVALID")
        if path.stat().st_size < 1 or path.stat().st_size > self.max_bytes:
            raise PresentationError("PPTX_SIZE_INVALID")
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if (len(members) > 20_000 or sum(item.file_size for item in members) > 500 * 1024 * 1024 or
                        any(item.file_size > 100 * 1024 * 1024 for item in members)):
                    raise PresentationError("PPTX_EXPANSION_LIMIT_EXCEEDED")
                names = {_safe_member(name) for name in archive.namelist()}
                if "ppt/presentation.xml" not in names:
                    raise PresentationError("PPTX_MANIFEST_MISSING")
                slides = self._ordered_slides(archive)
                return [self._parse_slide(archive, source, index) for index, source in enumerate(slides, 1)]
        except zipfile.BadZipFile as exc:
            raise PresentationError("PPTX_MALFORMED") from exc

    def _ordered_slides(self, archive: zipfile.ZipFile) -> list[str]:
        try:
            root = ET.fromstring(archive.read("ppt/presentation.xml"))
        except ET.ParseError as exc:
            raise PresentationError("PPTX_XML_INVALID") from exc
        rels = _relationships(archive, "ppt/presentation.xml")
        slides = []
        for node in root.iter(f"{{{PML}}}sldId"):
            rel_id = node.get(f"{{{REL}}}id")
            if not rel_id or rel_id not in rels:
                raise PresentationError("PPTX_SLIDE_RELATIONSHIP_INVALID")
            target, kind = rels[rel_id]
            if not kind.endswith("/slide"):
                raise PresentationError("PPTX_SLIDE_RELATIONSHIP_INVALID")
            slides.append(target)
        if not slides:
            raise PresentationError("PPTX_NO_SLIDES")
        return slides

    def _parse_slide(self, archive: zipfile.ZipFile, source: str, number: int) -> ParsedSlide:
        try:
            root = ET.fromstring(archive.read(source))
        except (KeyError, ET.ParseError) as exc:
            raise PresentationError("PPTX_SLIDE_INVALID") from exc
        title: list[str] = []
        body: list[str] = []
        other: list[str] = []
        alt: list[str] = []
        for shape in root.iter(f"{{{PML}}}sp"):
            values = [node.text.strip() for node in shape.iter(f"{{{DRAWING}}}t") if node.text and node.text.strip()]
            placeholder = next(shape.iter(f"{{{PML}}}ph"), None)
            kind = placeholder.get("type") if placeholder is not None else None
            if kind in {"title", "ctrTitle"}:
                title.extend(values)
            elif kind in {"body", "subTitle", "obj"}:
                body.extend(values)
            else:
                other.extend(values)
            for node in shape.iter(f"{{{PML}}}cNvPr"):
                for key in ("title", "descr"):
                    if node.get(key):
                        alt.append(node.get(key, "").strip())
        rels = _relationships(archive, source)
        notes = ""
        chart_text: list[str] = []
        for target, kind in rels.values():
            if kind.endswith("/notesSlide"):
                try:
                    values = _text_values(archive.read(target))
                    notes = "\n".join(v for v in values if v not in {"Click to edit Master text styles"})
                except KeyError as exc:
                    raise PresentationError("PPTX_NOTES_INVALID") from exc
            elif kind.endswith("/chart"):
                try:
                    chart_text.extend(_text_values(archive.read(target)))
                except KeyError:
                    continue
        return ParsedSlide(number, "\n".join(title), tuple(body), tuple(other), notes, tuple(chart_text), tuple(alt))


class PresentationJob:
    STAGES = {"PENDING", "PARSED", "SCRIPT_READY", "VOICE_SELECTED", "AUDIO_READY", "SEGMENT_READY", "COMPLETED", "FAILED"}
    SUBDIRS = ("source", "slides", "scripts", "audio", "segments", "output", "logs")

    def __init__(self, job_id: str, root: Path | str = "/Users/jerson/AI/runtime/presentation-jobs"):
        if not JOB_ID_RE.fullmatch(job_id):
            raise PresentationError("JOB_ID_INVALID")
        self.base = Path(root)
        self.path = self.base / job_id

    def create(self, source: Path | str) -> dict:
        source_path = Path(source)
        PPTXParser().parse(source_path)
        self.base.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.base, 0o700)
        if self.base.is_symlink() or self.path.exists():
            raise PresentationError("JOB_WORKSPACE_INVALID")
        self.path.mkdir(mode=0o700)
        for name in self.SUBDIRS:
            (self.path / name).mkdir(mode=0o700)
        target = self.path / "source" / "presentation.pptx"
        shutil.copyfile(source_path, target, follow_symlinks=False)
        os.chmod(target, 0o600)
        manifest = {
            "schema_version": "0.1", "job_id": self.path.name, "stage": "PENDING",
            "source_path": "source/presentation.pptx", "source_sha256": hash_file(target),
            "failures": [], "slides": [],
        }
        self.write_json("manifest.json", manifest)
        return manifest

    def update_stage(self, stage: str, **values) -> dict:
        if stage not in self.STAGES:
            raise PresentationError("JOB_STAGE_INVALID")
        manifest = self.read_json("manifest.json")
        manifest.update(values)
        manifest["stage"] = stage
        self.write_json("manifest.json", manifest)
        return manifest

    def fail(self, *, stage: str, category: str, detail: str, slide: int | None = None, retryable: bool = False) -> dict:
        manifest = self.read_json("manifest.json")
        manifest.setdefault("failures", []).append({
            "stage": stage, "slide": slide, "category": category,
            "detail": detail[:300], "retryable": retryable,
        })
        manifest["stage"] = "FAILED"
        self.write_json("manifest.json", manifest)
        return manifest

    def _contained(self, relative: str) -> Path:
        candidate = self.path / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise PresentationError("JOB_PATH_ESCAPE")
        if candidate.parent.resolve().is_relative_to(self.path.resolve()) is False:
            raise PresentationError("JOB_PATH_ESCAPE")
        return candidate

    def write_json(self, relative: str, value) -> None:
        target = self._contained(relative)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".job-", suffix=".json", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read_json(self, relative: str):
        target = self._contained(relative)
        if target.is_symlink() or not target.is_file():
            raise PresentationError("JOB_FILE_INVALID")
        try:
            return json.loads(target.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise PresentationError("JOB_JSON_INVALID") from exc
