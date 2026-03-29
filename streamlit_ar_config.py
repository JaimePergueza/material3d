import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "ar_items.json"
IMAGES_DIR = BASE_DIR / "imagenes"
MODELS_DIR = BASE_DIR / "modelos"

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_MODEL_EXTS = {".glb", ".gltf"}

DEFAULT_ITEMS = [
    {
        "targetIndex": 0,
        "imagen": "./imagenes/portada.jpg",
        "modelo": "./modelos/portada.glb",
        "titulo": "Frutas y Verduras",
        "escala": "0.7 0.7 0.7",
    },
    {
        "targetIndex": 1,
        "imagen": "./imagenes/proceso.jpg",
        "modelo": "./modelos/proceso.glb",
        "titulo": "Venta de productos",
        "escala": "0.7 0.7 0.7",
    },
]


def load_items() -> list[dict]:
    if not CONFIG_PATH.exists():
        return DEFAULT_ITEMS.copy()

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return DEFAULT_ITEMS.copy()

    if not isinstance(data, list):
        return DEFAULT_ITEMS.copy()

    return data


def normalize_items(rows: list[dict]) -> tuple[list[dict], list[str]]:
    normalized: list[dict] = []
    errors: list[str] = []
    used_target_indexes: set[int] = set()

    for idx, row in enumerate(rows, start=1):
        raw_target = row.get("targetIndex", "")
        raw_model = str(row.get("modelo", "")).strip()

        # Skip completely empty rows from the editor.
        if raw_target in ("", None) and not raw_model:
            continue

        try:
            target_index = int(raw_target)
        except (TypeError, ValueError):
            errors.append(f"Fila {idx}: targetIndex debe ser un numero entero.")
            continue

        if target_index < 0:
            errors.append(f"Fila {idx}: targetIndex no puede ser negativo.")
            continue

        if target_index in used_target_indexes:
            errors.append(f"Fila {idx}: targetIndex {target_index} esta repetido.")
            continue

        if not raw_model:
            errors.append(f"Fila {idx}: modelo es obligatorio.")
            continue

        used_target_indexes.add(target_index)

        normalized.append(
            {
                "targetIndex": target_index,
                "imagen": str(row.get("imagen", "")).strip(),
                "modelo": raw_model,
                "titulo": str(row.get("titulo", "")).strip(),
                "escala": str(row.get("escala", "0.7 0.7 0.7")).strip() or "0.7 0.7 0.7",
            }
        )

    normalized.sort(key=lambda item: item["targetIndex"])
    return normalized, errors


def save_items(items: list[dict]) -> None:
    CONFIG_PATH.write_text(
        json.dumps(items, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def ensure_asset_dirs() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)


def sanitize_filename(file_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", file_name.strip())
    cleaned = cleaned.strip(".-_")
    return cleaned or "archivo"


def unique_destination(folder: Path, file_name: str) -> Path:
    safe_name = sanitize_filename(file_name)
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix.lower()

    candidate = folder / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = folder / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def get_next_target_index(rows: list[dict]) -> int:
    max_value = -1
    for row in rows:
        raw_value = row.get("targetIndex", "")
        try:
            current = int(raw_value)
        except (TypeError, ValueError):
            continue

        if current > max_value:
            max_value = current

    return max_value + 1


def register_uploaded_pair(
    *,
    rows: list[dict],
    target_index: int,
    image_file,
    model_file,
    title: str,
    scale: str,
) -> tuple[bool, str, list[dict]]:
    if any(int(row.get("targetIndex")) == target_index for row in rows if str(row.get("targetIndex", "")).strip().isdigit()):
        return False, f"targetIndex {target_index} ya existe.", rows

    if image_file is None or model_file is None:
        return False, "Debes subir una imagen y un modelo.", rows

    image_ext = Path(image_file.name).suffix.lower()
    model_ext = Path(model_file.name).suffix.lower()

    if image_ext not in ALLOWED_IMAGE_EXTS:
        return False, f"Formato de imagen no valido: {image_ext}", rows

    if model_ext not in ALLOWED_MODEL_EXTS:
        return False, f"Formato de modelo no valido: {model_ext}", rows

    ensure_asset_dirs()

    image_dest = unique_destination(IMAGES_DIR, image_file.name)
    model_dest = unique_destination(MODELS_DIR, model_file.name)

    image_dest.write_bytes(image_file.getbuffer())
    model_dest.write_bytes(model_file.getbuffer())

    new_item = {
        "targetIndex": int(target_index),
        "imagen": f"./imagenes/{image_dest.name}",
        "modelo": f"./modelos/{model_dest.name}",
        "titulo": title.strip(),
        "escala": scale.strip() or "0.7 0.7 0.7",
    }

    new_rows = [*rows, new_item]
    normalized, validation_errors = normalize_items(new_rows)
    if validation_errors:
        return False, " ; ".join(validation_errors), rows

    save_items(normalized)
    return True, f"Par registrado: {image_dest.name} -> {model_dest.name}", normalized


def to_workspace_path(relative_path: str) -> Path | None:
    raw = str(relative_path or "").strip()
    if not raw.startswith("./"):
        return None

    candidate = (BASE_DIR / raw[2:]).resolve()
    try:
        candidate.relative_to(BASE_DIR.resolve())
    except ValueError:
        return None
    return candidate


def delete_item_and_assets(rows: list[dict], target_index: int, delete_assets: bool) -> tuple[bool, str, list[dict]]:
    item_to_delete = None
    kept_rows: list[dict] = []

    for row in rows:
        try:
            row_target = int(row.get("targetIndex"))
        except (TypeError, ValueError):
            kept_rows.append(row)
            continue

        if row_target == target_index and item_to_delete is None:
            item_to_delete = row
        else:
            kept_rows.append(row)

    if item_to_delete is None:
        return False, f"No existe targetIndex {target_index}.", rows

    normalized, validation_errors = normalize_items(kept_rows)
    if validation_errors:
        return False, " ; ".join(validation_errors), rows

    if delete_assets:
        # Elimina archivos solo si ya no se usan en los items restantes.
        for key in ("imagen", "modelo"):
            path_value = str(item_to_delete.get(key, "")).strip()
            if not path_value:
                continue

            still_used = any(str(row.get(key, "")).strip() == path_value for row in normalized)
            if still_used:
                continue

            abs_path = to_workspace_path(path_value)
            if abs_path and abs_path.exists() and abs_path.is_file():
                abs_path.unlink()

    save_items(normalized)
    return True, f"Item targetIndex {target_index} eliminado.", normalized


# ---------------------------------------------------------------------------
# Compilador targets.mind
# ---------------------------------------------------------------------------

COMPILE_SCRIPT = BASE_DIR / "compile_targets.js"
TARGETS_MIND   = BASE_DIR / "marcadores" / "targets.mind"


def _find_cmd(name: str) -> str | None:
    """Encuentra ejecutable; en Windows prueba tambien la extension .cmd."""
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
        [npm, "install"],
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        timeout=300,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def compile_targets(items: list[dict]) -> tuple[bool, str]:
    """Compila targets.mind con las imagenes de los items, en orden de targetIndex."""
    if not node_available():
        return False, "Node.js no esta disponible en el sistema."
    if not npm_deps_installed():
        return False, "Dependencias npm no instaladas. Usa el boton 'Instalar dependencias'."
    if not COMPILE_SCRIPT.exists():
        return False, f"No se encontro {COMPILE_SCRIPT.name}."

    sorted_items = sorted(items, key=lambda x: int(x.get("targetIndex", 0)))

    image_paths: list[str] = []
    for item in sorted_items:
        img_path = to_workspace_path(item.get("imagen", ""))
        if img_path is None or not img_path.exists():
            return False, f"Imagen no encontrada para targetIndex {item.get('targetIndex')}: {item.get('imagen')}"
        image_paths.append(str(img_path))

    if not image_paths:
        return False, "No hay imagenes configuradas para compilar."

    node = _find_cmd("node")
    cmd  = [node, str(COMPILE_SCRIPT)] + image_paths + [str(TARGETS_MIND)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "La compilacion tardo demasiado (>3 min). Intenta con menos imagenes o mas pequenas."

    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return False, f"Error al compilar:\n{output}"

    return True, f"targets.mind compilado con {len(image_paths)} imagen(es).\n{output}"


# ---------------------------------------------------------------------------

st.set_page_config(page_title="Config AR Libro", page_icon="AR", layout="wide")
st.title("Panel AR: imagen -> modelo")
st.caption(
    "Edita las relaciones entre imagen y modelo 3D. Al guardar, index.html las carga automaticamente desde ar_items.json."
)

if "rows" not in st.session_state:
    st.session_state.rows = load_items()

ensure_asset_dirs()

st.subheader("Subir y registrar automatico")
next_target = get_next_target_index(st.session_state.rows)

up_col_a, up_col_b = st.columns([1, 1])
with up_col_a:
    upload_target = st.number_input("targetIndex", min_value=0, value=next_target, step=1)
    upload_title = st.text_input("texto", value="")
with up_col_b:
    upload_scale = st.text_input("escala", value="0.7 0.7 0.7")

upload_image = st.file_uploader(
    "Imagen del libro",
    type=["png", "jpg", "jpeg", "webp"],
    accept_multiple_files=False,
)
upload_model = st.file_uploader(
    "Modelo 3D",
    type=["glb", "gltf"],
    accept_multiple_files=False,
)

preview_col_a, preview_col_b = st.columns([1, 1])
with preview_col_a:
    if upload_image is not None:
        st.caption("Vista previa de imagen")
        st.image(upload_image, use_container_width=True)
with preview_col_b:
    if upload_model is not None:
        size_kb = upload_model.size / 1024
        st.caption("Resumen de modelo")
        st.write(f"Archivo: {upload_model.name}")
        st.write(f"Tamano: {size_kb:.1f} KB")

if st.button("Subir archivos y registrar", type="primary", use_container_width=True):
    ok, message, updated_rows = register_uploaded_pair(
        rows=st.session_state.rows,
        target_index=int(upload_target),
        image_file=upload_image,
        model_file=upload_model,
        title=upload_title,
        scale=upload_scale,
    )

    if ok:
        st.session_state.rows = updated_rows
        st.success(message)
        st.rerun()
    else:
        st.error(message)

st.divider()
st.subheader("Eliminar item")

available_targets = sorted(
    {
        int(row.get("targetIndex"))
        for row in st.session_state.rows
        if str(row.get("targetIndex", "")).strip().isdigit()
    }
)

if available_targets:
    delete_target = st.selectbox("targetIndex a eliminar", options=available_targets)
    delete_assets = st.checkbox("Eliminar tambien imagen/modelo si ya no se usan", value=True)

    if st.button("Eliminar item seleccionado", type="secondary", use_container_width=True):
        ok, message, updated_rows = delete_item_and_assets(
            st.session_state.rows,
            int(delete_target),
            bool(delete_assets),
        )
        if ok:
            st.session_state.rows = updated_rows
            st.success(message)
            st.rerun()
        else:
            st.error(message)
else:
    st.info("No hay items para eliminar.")

st.divider()
st.subheader("Edicion manual")

col_a, col_b = st.columns([1, 1])
with col_a:
    if st.button("Recargar desde archivo", use_container_width=True):
        st.session_state.rows = load_items()

with col_b:
    if st.button("Agregar fila vacia", use_container_width=True):
        st.session_state.rows.append(
            {
                "targetIndex": "",
                "imagen": "",
                "modelo": "",
                "titulo": "",
                "escala": "0.7 0.7 0.7",
            }
        )

edited_rows = st.data_editor(
    st.session_state.rows,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "targetIndex": st.column_config.NumberColumn("targetIndex", step=1, min_value=0, required=True),
        "imagen": st.column_config.TextColumn("imagen", help="Referencia de imagen del libro."),
        "modelo": st.column_config.TextColumn("modelo", required=True, help="Ruta del .glb o .gltf."),
        "titulo": st.column_config.TextColumn("texto"),
        "escala": st.column_config.TextColumn("escala", help="Formato: x y z. Ejemplo: 0.7 0.7 0.7"),
    },
    hide_index=True,
)

st.session_state.rows = edited_rows

if st.button("Guardar configuracion", type="primary", use_container_width=True):
    cleaned, validation_errors = normalize_items(edited_rows)

    if validation_errors:
        for err in validation_errors:
            st.error(err)
    else:
        save_items(cleaned)
        st.success(f"Configuracion guardada en {CONFIG_PATH.name} con {len(cleaned)} item(s).")

st.divider()
st.subheader("Compilar targets.mind")
st.caption("Genera el archivo de marcadores que usa MindAR para detectar imagenes con la camara.")

# Estado del entorno
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

# Instalar dependencias (solo hace falta una vez)
if not npm_deps_installed():
    st.info("Primera vez: instala las dependencias de Node.js antes de compilar (puede tardar 1-2 min).")
    if st.button("Instalar dependencias (npm install)", use_container_width=True):
        with st.spinner("Ejecutando npm install..."):
            ok, msg = run_npm_install()
        if ok:
            st.success("Dependencias instaladas correctamente.")
            st.rerun()
        else:
            st.error(msg)

# Mostrar orden de imagenes que se compilaran
if st.session_state.rows:
    sorted_for_compile = sorted(
        [r for r in st.session_state.rows if str(r.get("targetIndex", "")).strip().isdigit()],
        key=lambda x: int(x["targetIndex"]),
    )
    if sorted_for_compile:
        st.markdown("**Imagenes que se compilaran (en este orden):**")
        for item in sorted_for_compile:
            img_path = to_workspace_path(item.get("imagen", ""))
            exists   = img_path is not None and img_path.exists()
            icon     = "✅" if exists else "❌"
            st.markdown(f"- `[{item['targetIndex']}]` {icon} `{item.get('imagen', '')}`")

# Boton de compilacion
if node_available() and npm_deps_installed():
    if st.button("Compilar targets.mind", type="primary", use_container_width=True):
        with st.spinner("Compilando... esto puede tardar unos segundos."):
            ok, msg = compile_targets(st.session_state.rows)
        if ok:
            st.success("targets.mind generado correctamente.")
            st.code(msg)
        else:
            st.error(msg)

st.divider()
st.subheader("Como usar")
st.markdown(
    "\n".join(
        [
            "1. Usa 'Subir y registrar automatico' para cargar imagen y modelo sin escribir rutas.",
            "2. El sistema guarda archivos en ./imagenes y ./modelos, y actualiza ar_items.json.",
            "3. targetIndex debe coincidir con el orden de imagenes en targets.mind.",
            "4. Recarga la pagina AR para ver los cambios.",
            "5. Si agregas nuevas imagenes objetivo, usa la seccion 'Compilar targets.mind' para regenerar el archivo de marcadores.",
        ]
    )
)
