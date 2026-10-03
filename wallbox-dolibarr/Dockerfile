ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
FROM ${BUILD_FROM}

# Build-Args (wurden früher über build.json übergeben)
ARG BUILD_VERSION=dev
ARG BUILD_ARCH=amd64
ARG BUILD_DATE
ARG BUILD_REF

LABEL \
  io.hass.name="ExpenseCharge" \
  io.hass.description="Schreibt RFID-Ladevorgänge von Wallboxen (HA-Sensoren oder OCPP 1.6J) direkt als Position in die Dolibarr-Spesenabrechnung des Mitarbeiters" \
  io.hass.type="addon" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.arch="${BUILD_ARCH}" \
  org.opencontainers.image.source="https://github.com/systemwerk-GmbH-Co-KG/ExpenseCharge"

# Für die Systeminfo der Oberfläche
ENV EXPENSECHARGE_VERSION="${BUILD_VERSION}"

# Alle Abhängigkeiten über apk. 5-fach-Retry gegen transiente Aussetzer.
#
# HINWEIS: Wenn dieser Schritt mit "dl-cdn.alpinelinux.org DNS: transient
# error" fehlschlägt, ist das DNS des HA-BUILD-Containers kaputt (häufig
# IPv6) — KEIN Fehler dieses Addons. Entweder Host-DNS reparieren
# (ha dns options --servers dns://1.1.1.1 && ha dns restart) ODER das
# vorgebaute Image aus GHCR nutzen (siehe image:-Feld in config.yaml,
# wird per GitHub Actions gebaut und vom HA-Host nur noch gepullt).
RUN for i in 1 2 3 4 5; do \
      echo "apk-Versuch $i/5..." && \
      apk add --no-cache \
        python3 \
        py3-aiohttp \
        py3-requests \
        py3-websockets \
        py3-jsonschema \
        py3-pip \
        sqlite-libs \
        ca-certificates \
      && break || { echo "apk fehlgeschlagen (Versuch $i) — warte 8s"; sleep 8; }; \
    done; \
    python3 -c "import aiohttp, requests, sqlite3; print('deps OK', __import__('sys').version)"

# ocpp (MIT) gibt es nicht als apk-Paket → gepinnt per pip. --no-deps: einzige
# Abhängigkeit jsonschema kommt oben aus apk (py3-jsonschema). websockets ist
# keine ocpp-Abhängigkeit und kommt ebenfalls aus apk (py3-websockets >= 14 nötig).
RUN pip install --no-cache-dir --break-system-packages --no-deps ocpp==2.1.0 \
    && apk del --no-cache py3-pip \
    && python3 -c "import ocpp, jsonschema, websockets; from websockets.asyncio.server import serve; \
assert int(websockets.__version__.split('.')[0]) >= 14, 'websockets %s zu alt (>=14 noetig)' % websockets.__version__; \
print('ocpp OK, websockets', websockets.__version__)"

# Addon-Dateien kopieren
COPY main.py            /usr/local/bin/main.py
COPY api_client.py      /usr/local/bin/api_client.py
COPY session_manager.py /usr/local/bin/session_manager.py
COPY web_server.py      /usr/local/bin/web_server.py
COPY wallbox_profile.py /usr/local/bin/wallbox_profile.py
COPY app_settings.py    /usr/local/bin/app_settings.py
COPY tag_release.py     /usr/local/bin/tag_release.py
COPY tag_learning.py    /usr/local/bin/tag_learning.py
COPY env_config.py      /usr/local/bin/env_config.py
COPY placeholders.py    /usr/local/bin/placeholders.py
COPY admin/             /usr/local/bin/admin/
COPY utils/             /usr/local/bin/utils/
COPY ocpp_server/       /usr/local/bin/ocpp_server/
COPY modbus_source/     /usr/local/bin/modbus_source/
COPY alfen_source/      /usr/local/bin/alfen_source/
RUN chmod a+x /usr/local/bin/main.py

CMD ["/usr/bin/with-contenv", "python3", "/usr/local/bin/main.py"]
