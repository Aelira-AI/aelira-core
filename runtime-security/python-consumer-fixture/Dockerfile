FROM ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7

ENV DEBIAN_FRONTEND=noninteractive PYTHONPATH=/app PIP_DISABLE_PIP_VERSION_CHECK=1
COPY frozen/ /fixture/frozen/
COPY system-packages.txt /fixture/system-packages.txt
COPY pins.txt /fixture/pins.txt
COPY requirements.txt /fixture/requirements.txt
COPY source-inventory.json /fixture/source-inventory.json
COPY src/ /app/src/
COPY verify.py /fixture/verify.py

# The bootstrap authenticated and locked these signed APT indexes and public
# PyPI wheels before construction. The image is built with --network none.
RUN cd /fixture/frozen \
 && sha256sum -c input.sha256 \
 && test "$(sha256sum /usr/share/keyrings/ubuntu-archive-keyring.gpg | cut -d ' ' -f1)" = "$(sha256sum /fixture/frozen/keyring/ubuntu-archive-keyring.gpg | cut -d ' ' -f1)" \
 && rm -f /etc/apt/apt.conf.d/docker-clean \
 && mkdir -p /var/lib/apt/lists /var/cache/apt/archives \
 && cp /fixture/frozen/apt-lists/* /var/lib/apt/lists/ \
 && cp /fixture/frozen/apt-debs/*.deb /var/cache/apt/archives/ \
 && apt-get install -y --no-download --no-install-recommends $(cat /fixture/system-packages.txt) \
 && python3 -m venv /opt/venv \
 && /opt/venv/bin/python -m pip install --no-index --no-deps \
      --find-links=/fixture/frozen/wheels -r /fixture/pins.txt \
      pillow==13.0.0 psycopg2-binary==2.9.13 \
 && /opt/venv/bin/python -m pip uninstall -y pip

WORKDIR /app
USER 1000:1000
CMD ["/opt/venv/bin/python", "/fixture/verify.py"]
