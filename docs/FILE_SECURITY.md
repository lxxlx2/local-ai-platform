# File security

Public uploads are isolated beneath `runtime/public-jobs/<uuid>/` using generated server filenames. Path traversal, absolute paths, symlink escapes, archives, executables, scripts, and extension mismatches are rejected centrally. V0.2 permits only `txt` and `md` planning flows. PDF, image understanding, audio, and video intelligence remain unavailable until separately installed and validated.

URLs are never automatically fetched. Telegram-provided files will only be accepted through the future allowlisted upload path.
