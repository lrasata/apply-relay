"""Build a service's Lambda package: its source plus its runtime dependencies.

    python scripts/build_lambda.py cv_parser

Writes services/<service>/build/package/, which Terraform zips. Dependencies are fetched
as Linux arm64 / Python 3.12 wheels to match the Lambda runtime, whatever OS you build on.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(service: str) -> Path:
    service_dir = ROOT / "services" / service
    if not (service_dir / "src").is_dir():
        sys.exit(f"unknown service: {service} (expected {service_dir / 'src'})")

    package = service_dir / "build" / "package"
    shutil.rmtree(package, ignore_errors=True)
    shutil.copytree(service_dir / "src", package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    requirements = service_dir / "requirements.txt"
    if requirements.exists():
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--quiet",
                "--requirement", str(requirements),
                "--target", str(package),
                "--platform", "manylinux2014_aarch64",
                "--implementation", "cp",
                "--python-version", "3.12",
                "--only-binary=:all:",
                "--upgrade",
            ],
            check=True,
        )
    return package


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(build(sys.argv[1]))
