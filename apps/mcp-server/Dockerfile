FROM node:22-slim AS viewer-assets
WORKDIR /assets
COPY apps/mcp-server/viewer-assets/package*.json ./
RUN npm ci --ignore-scripts --omit=dev

FROM python:3.12-slim

WORKDIR /app

COPY apps/mcp-server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Download the coastline used by the static map now, so requests never fetch it.
RUN python -c "import cartopy.io.shapereader as s; s.natural_earth(resolution='110m', category='physical', name='coastline')"

COPY apps/mcp-server/src /app/src
COPY --from=viewer-assets /assets/node_modules/cesium/Build/Cesium /app/viewer-assets/node_modules/cesium/Build/Cesium
COPY --from=viewer-assets /assets/node_modules/cesium/LICENSE.md /app/viewer-assets/CESIUM-LICENSE.md
COPY apps/mcp-server/data /app/data

EXPOSE 8000

CMD ["python", "-m", "src.server"]
