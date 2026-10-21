#!/usr/bin/env python3
"""Package JobPilot's extension; optional private seed stays outside Git.

python3 scripts/package_extension.py --output ~/job-search/JobPilot-Extension --zip
The output must be a new directory outside the repository. A verified profile
may be supplied as --profile; a PDF may be supplied as --resume.
"""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'extension'
FILES = ('manifest.json', 'background.js', 'model.js', 'actions.js', 'content.js',
         'sidepanel.html', 'sidepanel.js', 'sidepanel.css', 'README.md')


def package(output: Path, profile: Path | None = None, resume: Path | None = None,
            make_zip: bool = False) -> Path:
    output = output.expanduser().resolve()
    archive_path = output.parent / (output.name + '.zip')
    repo = SOURCE.parent.resolve()
    if output == repo or repo in output.parents:
        raise ValueError('Package into a new directory outside the repository.')
    if output.exists():
        raise ValueError('Output already exists; choose a new folder to preserve existing installs.')
    if make_zip and archive_path.exists():
        raise ValueError('ZIP already exists; choose a new output name.')
    for name in FILES:
        if not (SOURCE / name).is_file():
            raise ValueError(f'Missing extension source: {name}')
    manifest = json.loads((SOURCE / 'manifest.json').read_text())
    if manifest['manifest_version'] != 3 or len(manifest['description']) > 132:
        raise ValueError('Invalid extension manifest.')
    seed = None
    if profile:
        seed = json.loads(profile.expanduser().read_text())
        if not isinstance(seed, dict) or not isinstance(seed.get('work_history', []), list):
            raise ValueError('Supply a flat, verified extension profile JSON object.')
    resume_bytes = None
    if resume:
        resume = resume.expanduser().resolve()
        resume_bytes = resume.read_bytes()
        if resume.suffix.lower() != '.pdf' or not resume_bytes.startswith(b'%PDF-'):
            raise ValueError('Résumé must be a PDF file.')
        if not 0 < len(resume_bytes) <= 10 * 1024 * 1024:
            raise ValueError('Résumé exceeds the 10 MB limit.')
    output.mkdir(parents=True)
    for name in FILES:
        shutil.copyfile(SOURCE / name, output / name)
    if seed is not None or resume_bytes is not None:
        initial = {'profile': seed or {}, 'resumes': []}
        if resume_bytes is not None:
            (output / 'initial-resume.pdf').write_bytes(resume_bytes)
            initial['resumes'].append({'path': 'initial-resume.pdf', 'name': resume.name,
                                      'label': 'Software Engineer'})
        (output / 'initial-data.json').write_text(json.dumps(initial, indent=2) + '\n')
        (output / 'initial-data.json').chmod(0o600)
        if resume_bytes is not None:
            (output / 'initial-resume.pdf').chmod(0o600)
    if make_zip:
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(output.iterdir()):
                archive.write(file, file.name)
        if seed is not None or resume_bytes is not None:
            archive_path.chmod(0o600)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--zip', action='store_true', dest='make_zip')
    args = parser.parse_args()
    try:
        result = package(args.output, args.profile, args.resume, args.make_zip)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, f'Package failed: {error}\n')
    print(result)


if __name__ == '__main__':
    main()
