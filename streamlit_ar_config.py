import base64
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

BASE_DIR      = Path(__file__).resolve().parent
DB_PATH       = BASE_DIR / "ar_database.sqlite"
PROYECTOS_DIR = BASE_DIR / "proyectos"
COMPILE_SCRIPT = BASE_DIR / "compile_targets.js"

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_MODEL_EXTS = {".glb", ".gltf"}


# ──────────────────────────────────────────────
# Base de datos
# ──────────────────────────────────────────────

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS proyectos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre      TEXT UNIQUE NOT NULL,
                descripcion TEXT DEFAULT '',
                creado_en   TEXT DEFAULT (datetime('now'))
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                proyecto_id  INTEGER NOT NULL,
                target_index INTEGER NOT NULL,
                imagen       TEXT NOT NULL,
                modelo       TEXT NOT NULL,
                titulo       TEXT DEFAULT '',
                escala       TEXT DEFAULT '0.7 0.7 0.7',
                FOREIGN KEY (proyecto_id) REFERENCES proyectos(id) ON DELETE CASCADE,
                UNIQUE (proyecto_id, target_index)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        con.commit()

        # Migrar: agregar columnas nuevas si no existen
        for col, default in [("posicion", "0 0 0"), ("rotacion", "0 0 0")]:
            try:
                con.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT DEFAULT '{default}'")
                con.commit()
            except sqlite3.OperationalError:
                pass  # ya existe

        # Primera vez: migrar proyecto existente
        if con.execute("SELECT COUNT(*) FROM proyectos").fetchone()[0] == 0:
            _migrate_existing_project(con)


def get_setting(key: str, default: str = "") -> str:
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        con.commit()


def _migrate_existing_project(con: sqlite3.Connection) -> None:
    config_path = BASE_DIR / "ar_items.json"
    if not config_path.exists():
        return
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, list) or not data:
        return

    cur = con.execute(
        "INSERT INTO proyectos (nombre, descripcion) VALUES (?, ?)",
        ("material3d", "Proyecto inicial")
    )
    pid = cur.lastrowid
    for item in data:
        con.execute(
            "INSERT OR IGNORE INTO items (proyecto_id, target_index, imagen, modelo, titulo, escala) VALUES (?,?,?,?,?,?)",
            (pid, int(item.get("targetIndex", 0)), item.get("imagen", ""),
             item.get("modelo", ""), item.get("titulo", ""), item.get("escala", "0.7 0.7 0.7"))
        )
    con.commit()


def get_proyectos() -> list[dict]:
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT id, nombre, descripcion, creado_en FROM proyectos ORDER BY id"
        ).fetchall()]


def create_proyecto(nombre: str, descripcion: str) -> tuple[bool, str, int | None]:
    nombre = nombre.strip()
    if not nombre:
        return False, "El nombre no puede estar vacio.", None
    if not re.match(r'^[A-Za-z0-9_-]+$', nombre):
        return False, "Solo letras, numeros, guiones y guiones bajos.", None
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.execute(
                "INSERT INTO proyectos (nombre, descripcion) VALUES (?, ?)",
                (nombre, descripcion.strip())
            )
            pid = cur.lastrowid
            con.commit()
        root = PROYECTOS_DIR / nombre
        (root / "imagenes").mkdir(parents=True, exist_ok=True)
        (root / "modelos").mkdir(parents=True, exist_ok=True)
        (root / "ar_items.json").write_text("[]", encoding="utf-8")
        return True, f"Proyecto '{nombre}' creado.", pid
    except sqlite3.IntegrityError:
        return False, f"Ya existe un proyecto con el nombre '{nombre}'.", None


def delete_proyecto(proyecto_id: int, delete_files: bool) -> tuple[bool, str]:
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute("SELECT nombre FROM proyectos WHERE id = ?", (proyecto_id,)).fetchone()
        if not row:
            return False, "Proyecto no encontrado."
        nombre = row[0]
        con.execute("DELETE FROM proyectos WHERE id = ?", (proyecto_id,))
        con.commit()
    if delete_files and nombre != "material3d":
        proj_dir = PROYECTOS_DIR / nombre
        if proj_dir.exists():
            shutil.rmtree(proj_dir)
    sync_global_json()
    return True, f"Proyecto '{nombre}' eliminado."


def get_items(proyecto_id: int) -> list[dict]:
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        return [
            {
                "targetIndex": r["target_index"],
                "imagen": r["imagen"],
                "modelo": r["modelo"],
                "titulo": r["titulo"],
                "escala": r["escala"],
                "posicion": r["posicion"] or "0 0 0",
                "rotacion": r["rotacion"] or "0 0 0",
            }
            for r in con.execute(
                "SELECT target_index, imagen, modelo, titulo, escala, posicion, rotacion FROM items "
                "WHERE proyecto_id = ? ORDER BY target_index",
                (proyecto_id,)
            ).fetchall()
        ]


def save_item(proyecto_id: int, item: dict) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT OR REPLACE INTO items (proyecto_id, target_index, imagen, modelo, titulo, escala, posicion, rotacion) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (proyecto_id, int(item["targetIndex"]), item["imagen"],
             item["modelo"], item.get("titulo", ""), item.get("escala", "0.7 0.7 0.7"),
             item.get("posicion", "0 0 0"), item.get("rotacion", "0 0 0"))
        )
        con.commit()


def delete_item_db(proyecto_id: int, target_index: int, delete_files: bool = False) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        # Obtener item antes de borrar (para eliminar archivos)
        item_row = con.execute(
            "SELECT imagen, modelo FROM items WHERE proyecto_id = ? AND target_index = ?",
            (proyecto_id, target_index)
        ).fetchone()

        con.execute(
            "DELETE FROM items WHERE proyecto_id = ? AND target_index = ?",
            (proyecto_id, target_index)
        )
        con.commit()

        if delete_files and item_row:
            # Solo borrar si ningun otro item usa el mismo archivo
            remaining_imgs = {r[0] for r in con.execute(
                "SELECT imagen FROM items WHERE proyecto_id = ?", (proyecto_id,)
            ).fetchall()}
            remaining_mods = {r[0] for r in con.execute(
                "SELECT modelo FROM items WHERE proyecto_id = ?", (proyecto_id,)
            ).fetchall()}

            if item_row["imagen"] not in remaining_imgs:
                img_path = resolve_path(item_row["imagen"])
                if img_path and img_path.exists():
                    img_path.unlink()

            if item_row["modelo"] not in remaining_mods:
                mod_path = resolve_path(item_row["modelo"])
                if mod_path and mod_path.exists():
                    mod_path.unlink()


def replace_all_items(proyecto_id: int, items: list[dict]) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM items WHERE proyecto_id = ?", (proyecto_id,))
        for row in items:
            try:
                con.execute(
                    "INSERT INTO items (proyecto_id, target_index, imagen, modelo, titulo, escala, posicion, rotacion) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (proyecto_id, int(row["targetIndex"]), row.get("imagen", ""),
                     row.get("modelo", ""), row.get("titulo", ""), row.get("escala", "0.7 0.7 0.7"),
                     row.get("posicion", "0 0 0"), row.get("rotacion", "0 0 0"))
                )
            except Exception:
                pass
        con.commit()


def sync_json(proyecto_id: int, dirs: dict) -> None:
    items = get_items(proyecto_id)
    dirs["ar_items"].write_text(
        json.dumps(items, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    sync_global_json()


def sync_global_json() -> None:
    """Escribe el ar_items.json raíz con TODOS los items de TODOS los proyectos,
    ordenados por targetIndex. Esto mantiene sincronía con el targets.mind global."""
    all_items: list[dict] = []
    for proyecto in get_proyectos():
        all_items.extend(get_items(proyecto["id"]))
    all_items.sort(key=lambda x: int(x.get("targetIndex", 0)))
    global_json = BASE_DIR / "ar_items.json"
    global_json.write_text(
        json.dumps(all_items, ensure_ascii=True, indent=2), encoding="utf-8"
    )


# ──────────────────────────────────────────────
# Rutas de proyecto
# ──────────────────────────────────────────────

def get_project_dirs(nombre: str) -> dict:
    """Devuelve rutas absolutas y prefijos de ruta para un proyecto."""
    if nombre == "material3d":
        return {
            "root":       BASE_DIR,
            "imagenes":   BASE_DIR / "imagenes",
            "modelos":    BASE_DIR / "modelos",
            "marcadores": BASE_DIR / "marcadores",
            "ar_items":   BASE_DIR / "ar_items.json",
            "img_prefix": "./imagenes",
            "mod_prefix": "./modelos",
        }
    root = PROYECTOS_DIR / nombre
    return {
        "root":       root,
        "imagenes":   root / "imagenes",
        "modelos":    root / "modelos",
        "ar_items":   root / "ar_items.json",
        "img_prefix": f"./proyectos/{nombre}/imagenes",
        "mod_prefix": f"./proyectos/{nombre}/modelos",
    }


def resolve_path(relative: str) -> Path | None:
    """Resuelve una ruta relativa (./) a ruta absoluta desde BASE_DIR."""
    raw = str(relative or "").strip()
    if not raw.startswith("./"):
        return None
    candidate = (BASE_DIR / raw[2:]).resolve()
    try:
        candidate.relative_to(BASE_DIR.resolve())
    except ValueError:
        return None
    return candidate


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip())
    return cleaned.strip(".-_") or "archivo"


def unique_destination(folder: Path, file_name: str) -> Path:
    safe = sanitize_filename(file_name)
    stem, suffix = Path(safe).stem, Path(safe).suffix.lower()
    candidate = folder / f"{stem}{suffix}"
    n = 1
    while candidate.exists():
        candidate = folder / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def get_next_target_index_global() -> int:
    """Siguiente targetIndex disponible a nivel GLOBAL (todos los proyectos)."""
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute("SELECT MAX(target_index) FROM items").fetchone()
        return (row[0] + 1) if row[0] is not None else 0


# ──────────────────────────────────────────────
# Compilador targets.mind
# ──────────────────────────────────────────────

def _find_cmd(name: str) -> str | None:
    found = shutil.which(name)
    if found is None and sys.platform == "win32":
        found = shutil.which(name + ".cmd")
    return found


def node_available() -> bool:
    return _find_cmd("node") is not None


def npm_deps_installed() -> bool:
    return (BASE_DIR / "node_modules" / "mind-ar").exists()


def run_npm_install() -> tuple[bool, str]:
    npm = _find_cmd("npm")
    if npm is None:
        return False, "npm no encontrado. Verifica la instalacion de Node.js."
    result = subprocess.run(
        [npm, "install"], capture_output=True, text=True,
        cwd=str(BASE_DIR), timeout=300,
    )
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def compile_targets_global() -> tuple[bool, str]:
    """Compila UN SOLO targets.mind con las imagenes de TODOS los proyectos."""
    if not node_available():
        return False, "Node.js no disponible."
    if not npm_deps_installed():
        return False, "Dependencias npm no instaladas. Usa 'Instalar dependencias'."
    if not COMPILE_SCRIPT.exists():
        return False, f"No se encontro {COMPILE_SCRIPT.name}."

    # Recopilar items de TODOS los proyectos
    all_items: list[dict] = []
    for proyecto in get_proyectos():
        all_items.extend(get_items(proyecto["id"]))

    # Ordenar por targetIndex
    all_items.sort(key=lambda x: int(x.get("targetIndex", 0)))

    # Verificar que no haya targetIndex duplicados
    indexes = [int(i["targetIndex"]) for i in all_items]
    if len(indexes) != len(set(indexes)):
        dupes = [idx for idx in set(indexes) if indexes.count(idx) > 1]
        return False, f"Hay targetIndex duplicados entre proyectos: {dupes}. Corrige antes de compilar."

    image_paths: list[str] = []
    for item in all_items:
        img_path = resolve_path(item.get("imagen", ""))
        if img_path is None or not img_path.exists():
            return False, f"Imagen no encontrada para targetIndex {item.get('targetIndex')}: {item.get('imagen')}"
        image_paths.append(str(img_path))

    if not image_paths:
        return False, "No hay imagenes en ningun proyecto."

    targets_mind = BASE_DIR / "marcadores" / "targets.mind"
    (BASE_DIR / "marcadores").mkdir(parents=True, exist_ok=True)

    node = _find_cmd("node")
    cmd = [node, str(COMPILE_SCRIPT)] + image_paths + [str(targets_mind)]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(BASE_DIR), timeout=300
        )
    except subprocess.TimeoutExpired:
        return False, "La compilacion tardo demasiado (>5 min)."

    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return False, f"Error al compilar:\n{output}"
    sync_global_json()
    return True, f"targets.mind compilado con {len(image_paths)} imagen(es) de {len(get_proyectos())} proyecto(s).\n{output}"


# ──────────────────────────────────────────────
# Previsualizador 3D
# ──────────────────────────────────────────────

def _parse_vec3(val: str, default: tuple = (0, 0, 0)) -> tuple:
    try:
        parts = val.strip().split()
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:
        return default


def build_3d_viewer_html(
    model_b64: str,
    image_b64: str | None,
    image_ext: str,
    sx: float, sy: float, sz: float,
    px: float, py: float, pz: float,
    rx: float, ry: float, rz: float,
) -> str:
    img_section = ""
    if image_b64:
        img_section = f"""
        // Marker image as ground plane
        const imgTex = new THREE.TextureLoader().load('data:image/{image_ext};base64,{image_b64}');
        imgTex.colorSpace = THREE.SRGBColorSpace;
        const planeG = new THREE.PlaneGeometry(1, 1);
        const planeM = new THREE.MeshBasicMaterial({{ map: imgTex, transparent: true, opacity: 0.85, side: THREE.DoubleSide }});
        const plane = new THREE.Mesh(planeG, planeM);
        plane.rotation.x = -Math.PI / 2;
        plane.position.y = 0.001;
        scene.add(plane);
        """

    return f"""<!DOCTYPE html>
<html><head><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#1a1a2e; font-family:Arial,sans-serif; color:#e0e0e0; overflow:hidden; display:flex; height:100vh; }}
#viewport {{ flex:1; position:relative; }}
#panel {{ width:260px; background:#16213e; padding:16px; overflow-y:auto; border-left:1px solid #0f3460; }}
#panel h3 {{ color:#4299e1; margin:0 0 12px; font-size:14px; }}
.section {{ margin-bottom:16px; }}
.section-title {{ font-size:11px; color:#8899aa; text-transform:uppercase; letter-spacing:1px; margin-bottom:8px; }}
.control {{ display:flex; align-items:center; gap:8px; margin-bottom:6px; }}
.control label {{ width:16px; font-size:13px; font-weight:bold; color:#66aadd; }}
.control input[type=range] {{ flex:1; accent-color:#4299e1; }}
.control .val {{ width:50px; text-align:right; font-size:12px; font-family:monospace; color:#aaddff; }}
.values-box {{ background:#0f3460; border-radius:6px; padding:10px; margin-top:10px; font-family:monospace; font-size:12px; line-height:1.8; }}
.values-box span {{ color:#4299e1; }}
.copy-btn {{ background:#4299e1; color:#fff; border:none; border-radius:4px; padding:4px 10px; font-size:11px; cursor:pointer; margin-top:6px; }}
.copy-btn:hover {{ background:#3182ce; }}
#info {{ position:absolute; bottom:8px; left:8px; font-size:11px; color:#556; }}
</style></head>
<body>
<div id="viewport"><div id="info">Click + arrastrar para rotar | Scroll para zoom</div></div>
<div id="panel">
  <h3>Propiedades</h3>
  <div class="section">
    <div class="section-title">Escala</div>
    <div class="control"><label>X</label><input type="range" id="sx" min="0.01" max="5" step="0.01" value="{sx}"><span class="val" id="sx_v">{sx}</span></div>
    <div class="control"><label>Y</label><input type="range" id="sy" min="0.01" max="5" step="0.01" value="{sy}"><span class="val" id="sy_v">{sy}</span></div>
    <div class="control"><label>Z</label><input type="range" id="sz" min="0.01" max="5" step="0.01" value="{sz}"><span class="val" id="sz_v">{sz}</span></div>
  </div>
  <div class="section">
    <div class="section-title">Posicion</div>
    <div class="control"><label>X</label><input type="range" id="px" min="-3" max="3" step="0.01" value="{px}"><span class="val" id="px_v">{px}</span></div>
    <div class="control"><label>Y</label><input type="range" id="py" min="-3" max="3" step="0.01" value="{py}"><span class="val" id="py_v">{py}</span></div>
    <div class="control"><label>Z</label><input type="range" id="pz" min="-3" max="3" step="0.01" value="{pz}"><span class="val" id="pz_v">{pz}</span></div>
  </div>
  <div class="section">
    <div class="section-title">Rotacion (grados)</div>
    <div class="control"><label>X</label><input type="range" id="rx" min="-180" max="180" step="1" value="{rx}"><span class="val" id="rx_v">{rx}</span></div>
    <div class="control"><label>Y</label><input type="range" id="ry" min="-180" max="180" step="1" value="{ry}"><span class="val" id="ry_v">{ry}</span></div>
    <div class="control"><label>Z</label><input type="range" id="rz" min="-180" max="180" step="1" value="{rz}"><span class="val" id="rz_v">{rz}</span></div>
  </div>
  <div class="values-box" id="values-display">
    <span>Escala:</span> {sx} {sy} {sz}<br>
    <span>Posicion:</span> {px} {py} {pz}<br>
    <span>Rotacion:</span> {rx} {ry} {rz}
  </div>
  <button class="copy-btn" onclick="copyValues()">Copiar valores</button>
</div>

<script type="importmap">
{{
  "imports": {{
    "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
  }}
}}
</script>
<script type="module">
import * as THREE from 'three';
import {{ GLTFLoader }} from 'three/addons/loaders/GLTFLoader.js';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';

const container = document.getElementById('viewport');
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1a2e);

const camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.01, 100);
camera.position.set(1.5, 1.5, 1.5);

const renderer = new THREE.WebGLRenderer({{ antialias: true }});
renderer.setSize(container.clientWidth, container.clientHeight);
renderer.setPixelRatio(window.devicePixelRatio);
renderer.outputColorSpace = THREE.SRGBColorSpace;
container.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;

// Lights
scene.add(new THREE.AmbientLight(0xffffff, 0.7));
const dLight = new THREE.DirectionalLight(0xffffff, 1.0);
dLight.position.set(3, 5, 4);
scene.add(dLight);
scene.add(new THREE.HemisphereLight(0x8899cc, 0x443322, 0.4));

// Grid + axes
scene.add(new THREE.GridHelper(4, 20, 0x334466, 0x222244));
scene.add(new THREE.AxesHelper(0.5));

{img_section}

// Load model
const modelB64 = document.getElementById('model-data').textContent;
const raw = atob(modelB64);
const arr = new Uint8Array(raw.length);
for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);

let model = null;
new GLTFLoader().parse(arr.buffer, '', (gltf) => {{
  model = gltf.scene;
  model.scale.set({sx}, {sy}, {sz});
  model.position.set({px}, {py}, {pz});
  model.rotation.set({rx}*Math.PI/180, {ry}*Math.PI/180, {rz}*Math.PI/180);
  scene.add(model);

  // Center camera target
  const box = new THREE.Box3().setFromObject(model);
  const center = box.getCenter(new THREE.Vector3());
  controls.target.copy(center);

  // Play animations
  if (gltf.animations.length > 0) {{
    const mixer = new THREE.AnimationMixer(model);
    gltf.animations.forEach(clip => mixer.clipAction(clip).play());
    window._mixer = mixer;
    window._clock = new THREE.Clock();
  }}
}});

// Slider binding
const ids = ['sx','sy','sz','px','py','pz','rx','ry','rz'];
ids.forEach(id => {{
  const el = document.getElementById(id);
  el.addEventListener('input', () => {{
    const v = parseFloat(el.value);
    document.getElementById(id+'_v').textContent = v;
    if (!model) return;
    if (id[0]==='s') model.scale[id[1]] = v;
    else if (id[0]==='p') model.position[id[1]] = v;
    else model.rotation[id[1]] = v * Math.PI / 180;
    updateDisplay();
  }});
}});

function updateDisplay() {{
  const g = id => parseFloat(document.getElementById(id).value);
  document.getElementById('values-display').innerHTML =
    `<span>Escala:</span> ${{g('sx')}} ${{g('sy')}} ${{g('sz')}}<br>` +
    `<span>Posicion:</span> ${{g('px')}} ${{g('py')}} ${{g('pz')}}<br>` +
    `<span>Rotacion:</span> ${{g('rx')}} ${{g('ry')}} ${{g('rz')}}`;
}}

function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  if (window._mixer) window._mixer.update(window._clock.getDelta());
  renderer.render(scene, camera);
}}
animate();

window.addEventListener('resize', () => {{
  camera.aspect = container.clientWidth / container.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(container.clientWidth, container.clientHeight);
}});

window.copyValues = function() {{
  const g = id => parseFloat(document.getElementById(id).value);
  const text = `Escala: ${{g('sx')}} ${{g('sy')}} ${{g('sz')}}\\nPosicion: ${{g('px')}} ${{g('py')}} ${{g('pz')}}\\nRotacion: ${{g('rx')}} ${{g('ry')}} ${{g('rz')}}`;
  navigator.clipboard.writeText(text).then(() => {{
    const btn = document.querySelector('.copy-btn');
    btn.textContent = 'Copiado!';
    setTimeout(() => btn.textContent = 'Copiar valores', 1500);
  }});
}};
</script>
<script type="text/plain" id="model-data">{model_b64}</script>
</body></html>"""


# ──────────────────────────────────────────────
# App Streamlit
# ──────────────────────────────────────────────

st.set_page_config(page_title="Material 3D — Panel AR", layout="wide")
st.title("Panel AR — Material 3D")

init_db()

# ── Sidebar: lista de proyectos ───────────────
proyectos = get_proyectos()

with st.sidebar:
    col_titulo, col_add = st.columns([3, 1])
    with col_titulo:
        st.subheader("Proyectos")
    with col_add:
        if st.button("＋", help="Nuevo proyecto", use_container_width=True):
            st.session_state.show_new_project = not st.session_state.get("show_new_project", False)

    # Formulario nuevo proyecto (se muestra al pulsar +)
    if st.session_state.get("show_new_project"):
        with st.form("form_nuevo_sidebar"):
            nuevo_nombre = st.text_input("Nombre", placeholder="biologia-3d")
            nueva_desc   = st.text_input("Descripcion", placeholder="AR para libro de biologia")
            col_ok, col_cancel = st.columns(2)
            with col_ok:
                submitted = st.form_submit_button("Crear", type="primary")
            with col_cancel:
                cancelado = st.form_submit_button("Cancelar")
            if submitted:
                ok, msg, _ = create_proyecto(nuevo_nombre, nueva_desc)
                if ok:
                    st.session_state.proyecto_sel = nuevo_nombre.strip()
                    st.session_state.show_new_project = False
                    st.rerun()
                else:
                    st.error(msg)
            if cancelado:
                st.session_state.show_new_project = False
                st.rerun()

    st.divider()

    # Lista de proyectos como items clickeables
    nombres = [p["nombre"] for p in proyectos]
    if not nombres:
        st.caption("Sin proyectos todavia.")
        proyecto_activo = None
    else:
        if "proyecto_sel" not in st.session_state or st.session_state.proyecto_sel not in nombres:
            st.session_state.proyecto_sel = nombres[0]

        for p in proyectos:
            is_active = p["nombre"] == st.session_state.proyecto_sel
            label = f"**{p['nombre']}**" if is_active else p["nombre"]
            prefix = "▶ " if is_active else "　"
            if st.button(
                f"{prefix}{p['nombre']}",
                key=f"sel_{p['id']}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state.proyecto_sel = p["nombre"]
                st.rerun()

        proyecto_activo = next(p for p in proyectos if p["nombre"] == st.session_state.proyecto_sel)

        st.divider()
        if proyecto_activo.get("descripcion"):
            st.caption(proyecto_activo["descripcion"])
        st.caption(f"Creado: {proyecto_activo['creado_en'][:10]}")

        # URL configurable
        vercel_url = get_setting("vercel_url", "https://material3d-chi.vercel.app")
        st.caption("URL del proyecto:")
        if proyecto_activo["nombre"] == "material3d":
            st.code(f"{vercel_url.rstrip('/')}/", language=None)
        else:
            st.code(f"{vercel_url.rstrip('/')}/?proyecto={proyecto_activo['nombre']}", language=None)

        with st.expander("Configurar URL de Vercel"):
            nueva_url = st.text_input(
                "URL base de Vercel",
                value=vercel_url,
                placeholder="https://mi-proyecto.vercel.app",
                label_visibility="collapsed",
            )
            if st.button("Guardar URL", use_container_width=True):
                set_setting("vercel_url", nueva_url.strip().rstrip("/"))
                st.rerun()


# ── Pestanas ───────────────────────────────────
tab1, tab2, tab3, tab_preview, tab4, tab5 = st.tabs([
    "Proyectos", "Subir contenido", "Items AR", "Vista previa 3D", "Compilar", "Ayuda"
])


# ═══════════════════════════════════════════════
# Tab 1 — Proyectos
# ═══════════════════════════════════════════════
with tab1:
    st.subheader("Proyectos AR")
    st.caption("Para crear un nuevo proyecto usa el boton + en la barra lateral.")

    for p in proyectos:
        n_items = len(get_items(p["id"]))
        is_active = p["nombre"] == st.session_state.get("proyecto_sel")

        col_info, col_del = st.columns([5, 1])
        with col_info:
            active_mark = " — **activo**" if is_active else ""
            st.markdown(
                f"**{p['nombre']}**{active_mark} &nbsp;·&nbsp; {n_items} items"
                + (f" &nbsp;·&nbsp; _{p['descripcion']}_" if p.get("descripcion") else "")
            )
            st.caption(f"Creado: {p['creado_en'][:10]}")
        with col_del:
            if p["nombre"] != "material3d":
                if st.button("Eliminar", key=f"del_p_{p['id']}", type="secondary"):
                    st.session_state[f"confirm_del_{p['id']}"] = True

        if st.session_state.get(f"confirm_del_{p['id']}"):
            del_files = st.checkbox(
                f"Eliminar archivos de '{p['nombre']}' del disco",
                key=f"delfiles_{p['id']}"
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Confirmar eliminacion", key=f"conf_{p['id']}", type="primary"):
                    ok, msg = delete_proyecto(p["id"], del_files)
                    if ok:
                        st.success(msg)
                        remaining = [x for x in proyectos if x["nombre"] != p["nombre"]]
                        st.session_state.proyecto_sel = remaining[0]["nombre"] if remaining else ""
                        st.rerun()
                    else:
                        st.error(msg)
            with c2:
                if st.button("Cancelar", key=f"cancel_{p['id']}"):
                    del st.session_state[f"confirm_del_{p['id']}"]
                    st.rerun()

        st.divider()


# ═══════════════════════════════════════════════
# Tab 2 — Subir contenido
# ═══════════════════════════════════════════════
with tab2:
    if not proyecto_activo:
        st.info("Selecciona o crea un proyecto primero.")
    else:
        st.subheader(f"Subir contenido — {proyecto_activo['nombre']}")
        items = get_items(proyecto_activo["id"])
        dirs  = get_project_dirs(proyecto_activo["nombre"])

        with st.form("form_subir"):
            col_a, col_b = st.columns(2)
            with col_a:
                upload_target = st.number_input(
                    "targetIndex", min_value=0,
                    value=get_next_target_index_global(), step=1
                )
                upload_title = st.text_input("Titulo del item")
            with col_b:
                upload_scale = st.text_input("Escala", value="0.7 0.7 0.7")
                upload_position = st.text_input("Posicion", value="0 0 0")
                upload_rotation = st.text_input("Rotacion", value="0 0 0")

            upload_image = st.file_uploader(
                "Imagen marcadora (foto del libro)",
                type=["png", "jpg", "jpeg", "webp"],
            )
            upload_model = st.file_uploader(
                "Modelo 3D (.glb / .gltf)",
                type=["glb", "gltf"],
            )

            if upload_image or upload_model:
                pv_a, pv_b = st.columns(2)
                with pv_a:
                    if upload_image:
                        st.image(upload_image, caption=upload_image.name, use_container_width=True)
                with pv_b:
                    if upload_model:
                        st.write(f"**{upload_model.name}**")
                        st.write(f"Tamano: {upload_model.size / 1024:.1f} KB")

            if st.form_submit_button("Guardar item", type="primary", use_container_width=True):
                err = None
                if upload_image is None or upload_model is None:
                    err = "Debes subir imagen y modelo."
                elif any(int(i["targetIndex"]) == int(upload_target) for i in items):
                    err = f"targetIndex {int(upload_target)} ya existe."
                elif Path(upload_image.name).suffix.lower() not in ALLOWED_IMAGE_EXTS:
                    err = f"Formato de imagen no valido: {Path(upload_image.name).suffix}"
                elif Path(upload_model.name).suffix.lower() not in ALLOWED_MODEL_EXTS:
                    err = f"Formato de modelo no valido: {Path(upload_model.name).suffix}"

                if err:
                    st.error(err)
                else:
                    dirs["imagenes"].mkdir(parents=True, exist_ok=True)
                    dirs["modelos"].mkdir(parents=True, exist_ok=True)

                    img_dest = unique_destination(dirs["imagenes"], upload_image.name)
                    mod_dest = unique_destination(dirs["modelos"], upload_model.name)
                    img_dest.write_bytes(upload_image.getbuffer())
                    mod_dest.write_bytes(upload_model.getbuffer())

                    new_item = {
                        "targetIndex": int(upload_target),
                        "imagen":  f"{dirs['img_prefix']}/{img_dest.name}",
                        "modelo":  f"{dirs['mod_prefix']}/{mod_dest.name}",
                        "titulo":  upload_title.strip(),
                        "escala":  upload_scale.strip() or "0.7 0.7 0.7",
                        "posicion": upload_position.strip() or "0 0 0",
                        "rotacion": upload_rotation.strip() or "0 0 0",
                    }
                    save_item(proyecto_activo["id"], new_item)
                    sync_json(proyecto_activo["id"], dirs)
                    st.success(f"Item guardado: {img_dest.name} -> {mod_dest.name}")
                    st.rerun()


# ═══════════════════════════════════════════════
# Tab 3 — Items AR
# ═══════════════════════════════════════════════
with tab3:
    if not proyecto_activo:
        st.info("Selecciona o crea un proyecto primero.")
    else:
        st.subheader(f"Items AR — {proyecto_activo['nombre']}")
        items = get_items(proyecto_activo["id"])
        dirs  = get_project_dirs(proyecto_activo["nombre"])

        if not items:
            st.info("Sin items. Sube contenido en la pestana 'Subir contenido'.")
        else:
            for item in items:
                label = f"[{item['targetIndex']}] {item.get('titulo') or item['imagen'].split('/')[-1]}"
                with st.expander(label):
                    col_img, col_info, col_del = st.columns([2, 3, 1])
                    with col_img:
                        img_abs = resolve_path(item["imagen"])
                        if img_abs and img_abs.exists():
                            st.image(str(img_abs), use_container_width=True)
                        else:
                            st.caption(f"`{item['imagen']}`")
                    with col_info:
                        st.write(f"**targetIndex:** {item['targetIndex']}")
                        st.write(f"**Modelo:** {item['modelo'].split('/')[-1]}")
                        st.write(f"**Escala:** {item['escala']}")
                        st.write(f"**Posicion:** {item.get('posicion', '0 0 0')}")
                        st.write(f"**Rotacion:** {item.get('rotacion', '0 0 0')}")
                        if item.get("titulo"):
                            st.write(f"**Titulo:** {item['titulo']}")
                    with col_del:
                        if st.button("Eliminar", key=f"del_item_{item['targetIndex']}"):
                            st.session_state[f"confirm_del_item_{item['targetIndex']}"] = True

                if st.session_state.get(f"confirm_del_item_{item['targetIndex']}"):
                    del_files = st.checkbox(
                        "Eliminar tambien imagen y modelo del disco",
                        value=True,
                        key=f"delfiles_item_{item['targetIndex']}"
                    )
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Confirmar", key=f"conf_item_{item['targetIndex']}", type="primary"):
                            delete_item_db(proyecto_activo["id"], item["targetIndex"], delete_files=del_files)
                            sync_json(proyecto_activo["id"], dirs)
                            del st.session_state[f"confirm_del_item_{item['targetIndex']}"]
                            st.rerun()
                    with c2:
                        if st.button("Cancelar", key=f"cancel_item_{item['targetIndex']}"):
                            del st.session_state[f"confirm_del_item_{item['targetIndex']}"]
                            st.rerun()

        st.divider()
        st.subheader("Edicion manual")

        edited = st.data_editor(
            items,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "targetIndex": st.column_config.NumberColumn("targetIndex", step=1, min_value=0, required=True),
                "imagen":      st.column_config.TextColumn("imagen"),
                "modelo":      st.column_config.TextColumn("modelo", required=True),
                "titulo":      st.column_config.TextColumn("titulo"),
                "escala":      st.column_config.TextColumn("escala"),
                "posicion":    st.column_config.TextColumn("posicion"),
                "rotacion":    st.column_config.TextColumn("rotacion"),
            },
            hide_index=True,
        )

        if st.button("Guardar cambios", type="primary", use_container_width=True):
            replace_all_items(proyecto_activo["id"], edited)
            sync_json(proyecto_activo["id"], dirs)
            st.success("Cambios guardados.")
            st.rerun()


# ═══════════════════════════════════════════════
# Tab Preview — Vista previa 3D
# ═══════════════════════════════════════════════
with tab_preview:
    if not proyecto_activo:
        st.info("Selecciona o crea un proyecto primero.")
    else:
        st.subheader(f"Vista previa 3D — {proyecto_activo['nombre']}")
        items_prev = get_items(proyecto_activo["id"])
        dirs_prev = get_project_dirs(proyecto_activo["nombre"])

        if not items_prev:
            st.info("Sin items. Sube contenido primero.")
        else:
            # Selector de item
            item_labels = [
                f"[{it['targetIndex']}] {it.get('titulo') or it['modelo'].split('/')[-1]}"
                for it in items_prev
            ]
            sel_idx = st.selectbox("Selecciona un item", range(len(items_prev)),
                                   format_func=lambda i: item_labels[i], key="preview_sel")
            sel_item = items_prev[sel_idx]

            model_path = resolve_path(sel_item["modelo"])
            image_path = resolve_path(sel_item["imagen"])

            if model_path is None or not model_path.exists():
                st.error(f"Modelo no encontrado: {sel_item['modelo']}")
            else:
                model_size_mb = model_path.stat().st_size / (1024 * 1024)
                if model_size_mb > 30:
                    st.warning(f"Modelo grande ({model_size_mb:.1f} MB). La vista previa puede tardar.")

                # Load / cache model in session
                cache_key = f"preview_b64_{sel_item['modelo']}"
                if cache_key not in st.session_state:
                    if st.button("Cargar modelo en visor", type="primary", use_container_width=True):
                        with st.spinner(f"Cargando modelo ({model_size_mb:.1f} MB)..."):
                            st.session_state[cache_key] = base64.b64encode(
                                model_path.read_bytes()
                            ).decode()
                        st.rerun()
                    st.caption("Haz clic para cargar el modelo 3D en el previsualizador.")
                else:
                    model_b64 = st.session_state[cache_key]

                    # Image for ground plane
                    image_b64 = None
                    image_ext = "png"
                    if image_path and image_path.exists():
                        image_ext = image_path.suffix.lstrip(".").lower()
                        if image_ext == "jpg":
                            image_ext = "jpeg"
                        image_b64 = base64.b64encode(image_path.read_bytes()).decode()

                    # Parse current values
                    sc = _parse_vec3(sel_item.get("escala", "0.7 0.7 0.7"), (0.7, 0.7, 0.7))
                    ps = _parse_vec3(sel_item.get("posicion", "0 0 0"))
                    rt = _parse_vec3(sel_item.get("rotacion", "0 0 0"))

                    # Render viewer
                    html = build_3d_viewer_html(
                        model_b64, image_b64, image_ext,
                        sc[0], sc[1], sc[2],
                        ps[0], ps[1], ps[2],
                        rt[0], rt[1], rt[2],
                    )
                    components.html(html, height=550)

                    st.caption(
                        "Ajusta los sliders en el panel derecho del visor. "
                        "Cuando estes conforme, copia los valores y pegalos abajo."
                    )

                    # Save form
                    st.divider()
                    st.subheader("Guardar transformacion")
                    col_s, col_p, col_r = st.columns(3)
                    with col_s:
                        new_scale = st.text_input(
                            "Escala (X Y Z)", value=sel_item.get("escala", "0.7 0.7 0.7"),
                            key="prev_scale"
                        )
                    with col_p:
                        new_pos = st.text_input(
                            "Posicion (X Y Z)", value=sel_item.get("posicion", "0 0 0"),
                            key="prev_pos"
                        )
                    with col_r:
                        new_rot = st.text_input(
                            "Rotacion (X Y Z)", value=sel_item.get("rotacion", "0 0 0"),
                            key="prev_rot"
                        )

                    if st.button("Guardar transformacion", type="primary", use_container_width=True):
                        sel_item["escala"] = new_scale.strip() or "0.7 0.7 0.7"
                        sel_item["posicion"] = new_pos.strip() or "0 0 0"
                        sel_item["rotacion"] = new_rot.strip() or "0 0 0"
                        save_item(proyecto_activo["id"], sel_item)
                        sync_json(proyecto_activo["id"], dirs_prev)
                        # Invalidate cache to reload with new values
                        del st.session_state[cache_key]
                        st.success("Transformacion guardada.")
                        st.rerun()

                    if st.button("Recargar visor", use_container_width=True):
                        del st.session_state[cache_key]
                        st.rerun()


# ═══════════════════════════════════════════════
# Tab 4 — Compilar
# ═══════════════════════════════════════════════
with tab4:
    st.subheader("Compilar targets.mind")
    st.caption(
        "Genera UN SOLO archivo de marcadores con las imagenes de TODOS los proyectos. "
        "MindAR usa este archivo para detectar imagenes con la camara."
    )

    col_node, col_deps = st.columns(2)
    with col_node:
        if node_available():
            st.success("Node.js disponible")
        else:
            st.error("Node.js no encontrado")
    with col_deps:
        if npm_deps_installed():
            st.success("Dependencias instaladas")
        else:
            st.warning("Dependencias npm no instaladas")

    if not npm_deps_installed():
        st.info("Primera vez: instala dependencias antes de compilar (puede tardar 1-2 min).")
        if st.button("Instalar dependencias (npm install)", use_container_width=True):
            with st.spinner("Ejecutando npm install..."):
                ok, msg = run_npm_install()
            if ok:
                st.success("Dependencias instaladas correctamente.")
                st.rerun()
            else:
                st.error(msg)

    # Mostrar TODAS las imagenes de TODOS los proyectos
    all_items_for_compile: list[tuple[str, dict]] = []
    for p in proyectos:
        for item in get_items(p["id"]):
            all_items_for_compile.append((p["nombre"], item))

    all_items_for_compile.sort(key=lambda x: int(x[1].get("targetIndex", 0)))

    if all_items_for_compile:
        st.markdown("**Imagenes a compilar (en orden de targetIndex, todos los proyectos):**")
        all_ok = True
        for proj_name, item in all_items_for_compile:
            img_path = resolve_path(item.get("imagen", ""))
            exists = img_path is not None and img_path.exists()
            if not exists:
                all_ok = False
            icon = "OK" if exists else "NO ENCONTRADA"
            st.markdown(
                f"- `[{item['targetIndex']}]` {icon} — "
                f"`{item['imagen'].split('/')[-1]}` — _{proj_name}_"
            )
        if not all_ok:
            st.warning("Algunas imagenes no se encontraron en disco.")

        # Verificar duplicados de targetIndex
        indexes = [int(x[1]["targetIndex"]) for x in all_items_for_compile]
        if len(indexes) != len(set(indexes)):
            dupes = [idx for idx in set(indexes) if indexes.count(idx) > 1]
            st.error(f"targetIndex duplicados entre proyectos: {dupes}. Corrige antes de compilar.")
    else:
        st.info("No hay items en ningun proyecto.")

    targets_path = BASE_DIR / "marcadores" / "targets.mind"
    if targets_path.exists():
        size_kb = targets_path.stat().st_size / 1024
        st.success(f"targets.mind actual: {size_kb:.0f} KB")
    else:
        st.warning("targets.mind no existe todavia.")

    if node_available() and npm_deps_installed() and all_items_for_compile:
        if st.button("Compilar targets.mind", type="primary", use_container_width=True):
            with st.spinner("Compilando... puede tardar unos segundos."):
                ok, msg = compile_targets_global()
            if ok:
                st.success("targets.mind generado correctamente.")
                st.code(msg)
            else:
                st.error(msg)


# ═══════════════════════════════════════════════
# Tab 5 — Ayuda
# ═══════════════════════════════════════════════
with tab5:
    st.subheader("Como usar")
    _base_url = get_setting("vercel_url", "https://material3d-chi.vercel.app")
    st.markdown(f"""
### Flujo de trabajo por proyecto

1. **Proyectos** — Crea un proyecto nuevo o selecciona uno existente en la barra lateral.
2. **Subir contenido** — Sube la imagen marcadora (foto del libro) y el modelo 3D `.glb`.
   - El `targetIndex` se asigna automaticamente.
3. **Items AR** — Revisa, edita o elimina items. Puedes ajustar escala y titulo aqui.
4. **Compilar** — Genera `targets.mind` para que MindAR detecte las imagenes con la camara.
   - Recompila solo cuando cambies las imagenes marcadoras.
5. **Git push** — Sube los cambios a GitHub y Vercel desplegara automaticamente.

### URLs en Vercel

| Proyecto | URL |
|----------|-----|
| `material3d` (raiz) | `{_base_url}/` |
| Cualquier otro | `{_base_url}/?proyecto=nombre` |

### Importante

- `targetIndex` debe coincidir con el orden de compilacion en `targets.mind`.
- Siempre recompila despues de cambiar imagenes marcadoras.
- Los modelos GLB pueden pesar bastante — considera optimizarlos antes de subir.
- Para cambiar la URL de Vercel usa el desplegable "Configurar URL de Vercel" en la barra lateral.
    """)
