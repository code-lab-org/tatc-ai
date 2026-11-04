// Scene data lives in the URL fragment, which is never sent to the HTTP server.
// No private scenario store, cookies, external assets, or worker patches are needed.
async function loadScene() {
  const fragment = location.hash.slice(1);
  if (!fragment.startsWith("v1."))
    throw new Error(
      "Missing or unsupported scene data. Run the visualization tool again.",
    );
  const encoded = fragment.slice(3);
  if (encoded.length > 2000000)
    throw new Error("Scene data exceeds the supported size.");
  const bytes = Uint8Array.from(
    atob(encoded.replaceAll("-", "+").replaceAll("_", "/")),
    (c) => c.charCodeAt(0),
  );
  const stream = new Blob([bytes])
    .stream()
    .pipeThrough(new DecompressionStream("gzip"));
  const reader = stream.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > 8000000)
        throw new Error("Scene data exceeds the supported size.");
      chunks.push(value);
    }
  } finally {
    await reader.cancel();
  }
  const data = JSON.parse(await new Blob(chunks).text());
  if (
    !data.dataset &&
    (!data.scenario ||
      !Array.isArray(data.tracks) ||
      !Array.isArray(data.points))
  )
    throw new Error("Invalid scene data.");
  return data;
}

window.tatcStudy = loadScene();
