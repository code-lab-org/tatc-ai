// Real TATC polygons and Cesium CPU tessellation; no WebGL substitution here.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const C = require("cesium");
const root = path.resolve(__dirname, "../../../..");
const cases = JSON.parse(execFileSync(process.env.TATC_PYTHON || "python3", ["-c",
  "import json; from tests.polar_fixtures import polar_cases; print(json.dumps(polar_cases()))"], {
    cwd: root, env: {...process.env, PYTHONPATH: path.join(root,"apps/mcp-server")},
    encoding: "utf8", maxBuffer: 8 * 1024 * 1024,
}));
for (const fixture of cases) test(`native ${fixture.name} triangulates without polar distortion`, () => {
  let area = 0, triangles = 0;
  const positions = (ring) => C.Cartesian3.fromDegreesArray(ring.flat());
  for (const rings of fixture.parts) {
    const mesh = C.PolygonGeometry.createGeometry(new C.PolygonGeometry({
      polygonHierarchy: new C.PolygonHierarchy(positions(rings[0]),
        rings.slice(1).map((ring) => new C.PolygonHierarchy(positions(ring)))),
    }));
    assert.ok(mesh);
    const vertices = mesh.attributes.position.values;
    assert.ok([...vertices].every(Number.isFinite));
    if (fixture.name !== "antimeridian") {
      for (let i = 0; i < vertices.length; i += 3) {
        const lat = C.Math.toDegrees(C.Cartographic.fromCartesian(C.Cartesian3.fromArray(vertices,i)).latitude);
        assert.ok(lat >= fixture.bounds[1] - 0.1 && lat <= fixture.bounds[3] + 0.1);
      }
    }
    for (let k = 0; k < mesh.indices.length; k += 3) {
      const [a,b,c] = Array.from(mesh.indices.slice(k,k+3),
        (i) => C.Cartesian3.fromArray(vertices,i*3));
      area += C.Cartesian3.magnitude(C.Cartesian3.cross(
        C.Cartesian3.subtract(b,a,new C.Cartesian3()),
        C.Cartesian3.subtract(c,a,new C.Cartesian3()),new C.Cartesian3())) / 2;
      triangles++;
    }
  }
  assert.ok(triangles > 0);
  // A rendering sanity check, not a scientific area/accuracy metric.
  assert.ok(Math.abs(area / fixture.area - 1) < 0.01, `mesh/native area ratio: ${area/fixture.area}`);
});

test("native pole-to-pole dissolved ground track tessellates in bounded patches", () => {
  const fixture = JSON.parse(execFileSync(process.env.TATC_PYTHON || "python3", ["-c",
    "import json; from tests.polar_fixtures import polar_dissolve_case; print(json.dumps(polar_dissolve_case()))"], {
    cwd: root, env: {...process.env, PYTHONPATH: path.join(root, "apps/mcp-server")},
    encoding: "utf8", maxBuffer: 8 * 1024 * 1024,
  }));
  const positions = (ring) => C.Cartesian3.fromDegreesArray(ring.flat());
  assert.ok(fixture.parts.length > 1);
  for (const rings of fixture.parts) {
    const coords = rings.flat();
    const lons = coords.map((point) => point[0]);
    const lats = coords.map((point) => point[1]);
    assert.ok(Math.max(...lons) - Math.min(...lons) <= 90 + 1e-8);
    assert.ok(Math.max(...lats) - Math.min(...lats) <= 90 + 1e-8);
    const mesh = C.PolygonGeometry.createGeometry(new C.PolygonGeometry({
      polygonHierarchy: new C.PolygonHierarchy(positions(rings[0]),
        rings.slice(1).map((ring) => new C.PolygonHierarchy(positions(ring)))),
    }));
    assert.ok(mesh && [...mesh.attributes.position.values].every(Number.isFinite));
  }
});
