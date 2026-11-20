# SignalClassifier project instructions

- Canonical repository: https://github.com/schildtaero/SignalClassifier.git. Preserve remote history and user changes; no force pushes. Commit and push completed verified work when implementing authorized updates here.
- Local Linux/WSL2 application, FastAPI and offline static German UI. Do not silently substitute synthetic data for unavailable SDR hardware.
- CPU works without PyTorch; neural CPU/CUDA is optional. Never label a proposed topology as empirically superior without recorded experiments.
- Preserve original capture groups across replay, clipping, export and import. Split train/validation/test by independent origin group before segmenting or fitting normalization.
- Distinguish provisional frequency hints, confirmed human labels and observed decoder evidence. Preserve actual sample-rate/center-frequency units and provenance. Digital dBFS is not calibrated dBm or absolute SNR.
- Do not claim universal drone RF/video decoding, authenticated Remote ID, hardware accuracy or real-time performance without corresponding tests. Show absent values as unknown.
- Runtime IQ, SQLite, model weights, venvs and credentials stay outside Git. Model imports must remain safe numeric/weights-only loaders and never execute supplied Python.
- Meaningful checks: `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 .venv/bin/python -m unittest discover -s tests -v`, `node --check signalclassifier/static/app.js`, `bash -n start.sh setup.sh install_driver.sh`. Installer tests must mock privileged/package/network commands; never execute the vendor installer during automated software checks. Physical SDR, Windows/WSL and CUDA acceptance are separate from synthetic software tests.
- Keep README, version, supported capability documentation and verification notes consistent with the delivered implementation.
