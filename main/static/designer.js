/* Template designer frontend.
 *
 * Loads a blank template image, then lets the user place/resize a dashed
 * photo box and any number of text fields (size / bold / colour / alignment /
 * wrapping). Dummy data is typed to preview the final card. Hitting "Save
 * template" POSTs the photo box + field layout to the server, which stores it
 * as the active template that every future batch run uses.
 */

const $ = (s) => document.querySelector(s);

let state = null;         // /api/state JSON
let name = null;
let canvas = null;
let canvasW = 0, canvasH = 0, displayScale = 1;
let photoObj = null;
let textObjs = {};        // key -> fabric.Textbox
let selectedKey = null;
let syncing = false;      // guard against control->object feedback loops

const FABRIC_CDN_OK = typeof fabric !== "undefined";

/* --------------------------------------------------------------- init --- */

function queryParam(key) {
  return new URLSearchParams(location.search).get(key);
}

async function boot() {
  name = queryParam("template");
  if (!name) {
    initPicker();
    return;
  }
  await loadTemplate(name);
}

async function loadTemplate(tplName) {
  name = tplName;
  $("#picker").hidden = true;
  $("#workspace").hidden = false;
  $("#template-name").textContent = name;

  let res;
  try {
    res = await fetch(`/api/state/${encodeURIComponent(name)}`);
  } catch (e) {
    alert("Could not reach the designer server. Check the terminal running --new.");
    return;
  }
  if (!res.ok) { alert("Template state failed to load (HTTP " + res.status + ")."); return; }
  state = await res.json();

  buildFieldsPanel();
  buildCanvas();
}

/* ------------------------------------------------------------ picker --- */

async function initPicker() {
  $("#workspace").hidden = true;
  $("#picker").hidden = false;
  $("#template-file").addEventListener("change", onFileChosen);

  try {
    const res = await fetch("/api/designs");
    const designs = res.ok ? await res.json() : [];
    if (designs.length) {
      $("#designs").hidden = false;
      const grid = $("#designs-grid");
      grid.innerHTML = "";
      for (const d of designs) {
        const item = document.createElement("div");
        item.className = "design-item" + (d.active ? " active" : "");
        item.innerHTML = `<img src="${d.thumb_url}" alt=""><div class="design-meta">
          <div class="design-name">${d.name}</div>
          <div class="design-sub">${d.active ? "Active template" : "Click to reopen"}</div>
        </div>`;
        item.addEventListener("click", () => {
          loadTemplate(d.name);
        });
        grid.appendChild(item);
      }
    }
  } catch (e) { /* designs list optional */ }
}

function showPickerMsg(text, isErr) {
  const el = $("#picker-msg");
  el.hidden = false;
  el.textContent = text;
  el.className = "picker-msg " + (isErr ? "err" : "");
}

async function onFileChosen() {
  const input = $("#template-file");
  const file = input.files && input.files[0];
  if (!file) return;
  showPickerMsg("Uploading “" + file.name + "”…", false);
  const fd = new FormData();
  fd.append("template", file);
  let res;
  try {
    res = await fetch("/api/start", { method: "POST", body: fd });
  } catch (e) {
    showPickerMsg("Upload failed (network). Check the terminal.", true);
    input.value = "";
    return;
  }
  const out = await res.json().catch(() => ({}));
  if (!res.ok || !out.name) {
    showPickerMsg("Upload failed: " + (out.error || "HTTP " + res.status), true);
    input.value = "";
    return;
  }
  input.value = "";
  showPickerMsg("Loading “" + out.name + "”…", false);
  await loadTemplate(out.name);
}

function labelFor(key) {
  if (state && state.fields) {
    const f = state.fields.find((x) => x.key === key);
    if (f) return f.label;
  }
  return key.replace(/_/g, " ");
}

/* ------------------------------------------------------------- canvas --- */

function computeFit() {
  const area = $("#canvas-area");
  const w = (area && area.clientWidth || 800) - 56;
  const h = (area && area.clientHeight || 700) - 56;
  if (!canvasW || !canvasH) return 1;
  return Math.min(1, w / canvasW, h / canvasH);
}

function applyZoom() {
  canvas.setZoom(displayScale);
  $("#canvas-shell").style.width = (canvasW * displayScale) + "px";
  $("#canvas-shell").style.height = (canvasH * displayScale) + "px";
  $("#zoom-label").textContent = Math.round(displayScale * 100) + "%";
}
function setZoom(z) { displayScale = Math.min(3, Math.max(0.15, z)); applyZoom(); }

$("#btn-zoom-in").addEventListener("click", () => setZoom(displayScale * 1.2));
$("#btn-zoom-out").addEventListener("click", () => setZoom(displayScale / 1.2));
$("#btn-zoom-fit").addEventListener("click", () => setZoom(Math.max(0.15, computeFit())));

async function buildCanvas() {
  canvasW = state.image_w; canvasH = state.image_h;
  const el = $("#fabric-canvas");
  canvas = new fabric.Canvas(el, { selection: false, preserveObjectStacking: true });
  canvas.setWidth(canvasW); canvas.setHeight(canvasH);
  displayScale = Math.max(0.15, computeFit());
  applyZoom();
  requestAnimationFrame(() => { displayScale = Math.max(0.15, computeFit()); applyZoom(); });

  canvas.setBackgroundImage(state.template_url, canvas.renderAll.bind(canvas),
    { crossOrigin: "anonymous" });

  addPhotoBox();
  for (const key of Object.keys(state.layout.texts)) {
    addTextObj(key);
  }

  canvas.on("selection:created", onSelectionChanged);
  canvas.on("selection:updated", onSelectionChanged);
  canvas.on("selection:cleared", hidePanels);

  canvas.on("object:moved", () => {
    if (canvas.getActiveObject() === photoObj) syncPhotoPanel();
    else if (selectedKey && textObjs[selectedKey]) syncSelectedTextPanel();
  });
  canvas.on("object:scaling", () => {
    if (canvas.getActiveObject() === photoObj) syncPhotoPanel();
  });

  canvas.requestRenderAll();
  window.__designer = { canvas, textObjs, photoObj, get state() { return state; } };
}

function selectedIsPhoto() { return canvas.getActiveObject() === photoObj; }

// Move the selected object with arrow keys (Shift = 10px, else 1px).
document.addEventListener("keydown", (e) => {
  if (!canvas || !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const obj = canvas.getActiveObject();
  if (!obj) return;
  if (obj.isEditing) return;
  const t = document.activeElement;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  e.preventDefault();
  const step = e.shiftKey ? 10 : 1;
  const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
  const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
  obj.set({ left: obj.left + dx, top: obj.top + dy });
  canvas.requestRenderAll();
  if (obj === photoObj) {
    state.layout.photo.x = Math.round(obj.left);
    state.layout.photo.y = Math.round(obj.top);
    syncPhotoPanel();
  } else if (obj.name && obj.name.startsWith("field:")) {
    const k = obj.name.slice(6);
    if (state.layout.texts[k]) {
      state.layout.texts[k].x = Math.round(obj.left);
      state.layout.texts[k].y = Math.round(obj.top);
    }
    syncSelectedTextPanel();
  }
});

/* ----------------------------------------------------------- objects --- */

function addPhotoBox() {
  const spec = state.layout.photo;
  const rect = new fabric.Rect({
    left: spec.x, top: spec.y,
    width: spec.width, height: spec.height,
    rx: spec.corner_radius || 0, ry: spec.corner_radius || 0,
    fill: "rgba(240,180,85,0.06)",
    stroke: "#f0b455", strokeWidth: 2, strokeDashArray: [7, 5],
    hasRotatingPoint: false, lockRotation: true,
    cornerColor: "#f0b455", cornerSize: 12, transparentCorners: false,
    borderColor: "#f0b455", borderScaleFactor: 2,
  });
  rect.setControlsVisibility({ mtr: false, tl: true, tr: true, bl: true, br: true });
  rect.name = "photo";
  photoObj = rect;
  canvas.add(rect);
  canvas.sendToBack(rect);
}

function addTextObj(key) {
  const spec = state.layout.texts[key] || {};
  const sample = (state.dummy[key] || labelFor(key) + " sample");
  const tb = new fabric.Textbox(sample, {
    left: spec.x || 20, top: spec.y || 20,
    width: spec.width || 300,
    fontSize: spec.font_size || 30,
    fill: spec.color || "#000000",
    fontWeight: spec.bold ? "bold" : "normal",
    textAlign: spec.align || "left",
    fontFamily: "DejaVu Sans, Arial, sans-serif",
    // Match PIL's anchor: the box top = the top of the first text line
    // (ascender), i.e. anchor "la". A 1.0 line-height and zero padding keep the
    // box tight against the glyphs so the (x, y) you see is what PIL draws.
    lineHeight: 1,
    padding: 0,
    visible: spec.enabled !== false,
    hasRotatingPoint: false, lockRotation: true,
    cornerColor: "#f0b455", cornerSize: 11, transparentCorners: false,
    borderColor: "#f0b455", borderScaleFactor: 2,
  });
  tb.setControlsVisibility({ mtr: false });
  tb.name = "field:" + key;
  tb.on("changed", () => {
    if (canvas.getActiveObject() === tb) { syncSelectedTextPanel(); canvas.requestRenderAll(); }
  });
  tb.on("editing:exited", () => { bakeTextObj(key, tb); canvas.requestRenderAll(); });
  canvas.add(tb);
  textObjs[key] = tb;
  canvas.bringToFront(tb);
}

/* --------------------------------------------------------- geometry ---- */

function bakeTextObj(key, obj) {
  const spec = state.layout.texts[key];
  if (!spec) return;
  const sx = obj.scaleX || 1, sy = obj.scaleY || 1;
  spec.x = Math.round(obj.left);
  spec.y = Math.round(obj.top);
  if (!spec.anchor) spec.anchor = "la";
  spec.width = Math.max(1, Math.round(obj.width * sx));
  spec.font_size = Math.max(4, Math.round(obj.fontSize * sy));
  if (spec.multiline) {
    spec.height = Math.max(20, Math.round((obj.height || 40) * sy));
  }
  obj.width = spec.width;
  obj.height = spec.height || obj.height;
  obj.scaleX = 1; obj.scaleY = 1;
  obj.initDimensions && obj.initDimensions();
  saveDummyFrom(key, obj);
}

function saveDummyFrom(key, obj) {
  let v = obj.text || "";
  const spec = state.layout.texts[key];
  if (spec && spec.prefix && v.startsWith(spec.prefix)) {
    v = v.slice(spec.prefix.length);
  }
  state.dummy[key] = v;
  const input = document.querySelector(`[data-dummy="${key}"]`);
  if (input && document.activeElement !== input) input.value = v;
}

function syncDummyToCanvas(key, value) {
  const o = textObjs[key];
  if (!o) return;
  const spec = state.layout.texts[key] || {};
  let txt = value;
  if (spec.uppercase || key === "student_name") txt = txt.toUpperCase();
  if (spec.prefix && txt) txt = spec.prefix + txt;
  o.set("text", txt);
  canvas.requestRenderAll();
}

/* ----------------------------------------------------------- panels ---- */

function onSelectionChanged() {
  const obj = canvas.getActiveObject();
  if (!obj) { hidePanels(); return; }
  if (obj === photoObj) {
    $("#photo-panel").hidden = false;
    $("#text-panel").hidden = true;
    selectedKey = null;
    syncPhotoPanel();
    return;
  }
  if (obj.name && obj.name.startsWith("field:")) {
    selectedKey = obj.name.slice(6);
    $("#photo-panel").hidden = true;
    $("#text-panel").hidden = false;
    syncSelectedTextPanel();
  }
}

function hidePanels() {
  $("#photo-panel").hidden = true;
  $("#text-panel").hidden = true;
  selectedKey = null;
}

function syncPhotoPanel() {
  if (!photoObj || syncing) return;
  syncing = true;
  const sx = photoObj.scaleX || 1, sy = photoObj.scaleY || 1;
  $("#photo-x").value = Math.round(photoObj.left);
  $("#photo-y").value = Math.round(photoObj.top);
  $("#photo-w").value = Math.round(photoObj.width * sx);
  $("#photo-h").value = Math.round(photoObj.height * sy);
  $("#photo-radius").value = state.layout.photo.corner_radius || 0;
  syncing = false;
}

function syncSelectedTextPanel() {
  if (!selectedKey || !textObjs[selectedKey] || syncing) return;
  const spec = state.layout.texts[selectedKey];
  const obj = textObjs[selectedKey];
  syncing = true;
  $("#text-key").textContent = selectedKey;
  $("#text-x").value = Math.round(obj.left);
  $("#text-y").value = Math.round(obj.top);
  $("#text-w").value = Math.round(obj.width * (obj.scaleX || 1));
  $("#text-size").value = Math.round(obj.fontSize * (obj.scaleY || 1));
  $("#text-min").value = spec.min_font_size || 12;
  $("#text-color").value = spec.color || "#000000";
  $("#text-bold").checked = !!spec.bold;
  $("#text-multiline").checked = !!spec.multiline;
  $("#text-align").value = spec.align || "left";
  syncing = false;
}

/* ------------------------------------------------- photo box controls --- */

$("#photo-x").addEventListener("input", (e) => {
  if (!photoObj) return;
  const v = parseInt(e.target.value, 10) || 0;
  photoObj.set("left", v); state.layout.photo.x = v; canvas.requestRenderAll();
});
$("#photo-y").addEventListener("input", (e) => {
  if (!photoObj) return;
  const v = parseInt(e.target.value, 10) || 0;
  photoObj.set("top", v); state.layout.photo.y = v; canvas.requestRenderAll();
});
$("#photo-w").addEventListener("input", (e) => {
  if (!photoObj) return;
  const v = Math.max(1, parseInt(e.target.value, 10) || 1);
  photoObj.set("width", v); state.layout.photo.width = v; canvas.requestRenderAll();
});
$("#photo-h").addEventListener("input", (e) => {
  if (!photoObj) return;
  const v = Math.max(1, parseInt(e.target.value, 10) || 1);
  photoObj.set("height", v); state.layout.photo.height = v; canvas.requestRenderAll();
});
$("#photo-radius").addEventListener("input", (e) => {
  const v = parseInt(e.target.value, 10) || 0;
  state.layout.photo.corner_radius = v;
  if (photoObj) { photoObj.set({ rx: v, ry: v }); canvas.requestRenderAll(); }
});

/* --------------------------------------------------- text field controls --- */

$("#text-x").addEventListener("input", (e) => {
  const o = textObjs[selectedKey]; if (!o) return;
  o.set("left", parseInt(e.target.value, 10) || 0);
  state.layout.texts[selectedKey].x = Math.round(o.left);
  canvas.requestRenderAll();
});
$("#text-y").addEventListener("input", (e) => {
  const o = textObjs[selectedKey]; if (!o) return;
  o.set("top", parseInt(e.target.value, 10) || 0);
  state.layout.texts[selectedKey].y = Math.round(o.top);
  canvas.requestRenderAll();
});
$("#text-w").addEventListener("input", (e) => {
  const o = textObjs[selectedKey]; if (!o) return;
  o.set("width", Math.max(1, parseInt(e.target.value, 10) || 1));
  state.layout.texts[selectedKey].width = Math.round(o.width);
  canvas.requestRenderAll();
});
$("#text-size").addEventListener("input", (e) => {
  const o = textObjs[selectedKey]; if (!o) return;
  o.set("fontSize", Math.max(4, parseInt(e.target.value, 10) || 10));
  state.layout.texts[selectedKey].font_size = Math.round(o.fontSize);
  o.initDimensions && o.initDimensions();
  canvas.requestRenderAll();
});
$("#text-min").addEventListener("input", (e) => {
  if (selectedKey) state.layout.texts[selectedKey].min_font_size = parseInt(e.target.value, 10) || 8;
});
$("#text-color").addEventListener("input", (e) => {
  const o = textObjs[selectedKey]; if (!o) return;
  o.set("fill", e.target.value);
  state.layout.texts[selectedKey].color = e.target.value;
  canvas.requestRenderAll();
});
$("#text-bold").addEventListener("change", (e) => {
  const o = textObjs[selectedKey]; if (!o) return;
  o.set("fontWeight", e.target.checked ? "bold" : "normal");
  state.layout.texts[selectedKey].bold = e.target.checked;
  canvas.requestRenderAll();
});
$("#text-multiline").addEventListener("change", (e) => {
  state.layout.texts[selectedKey].multiline = e.target.checked;
});
$("#text-align").addEventListener("change", (e) => {
  const o = textObjs[selectedKey]; if (!o) return;
  o.set("textAlign", e.target.value);
  state.layout.texts[selectedKey].align = e.target.value;
  canvas.requestRenderAll();
});
$("#btn-text-remove").addEventListener("click", removeSelectedField);

function removeSelectedField() {
  if (!selectedKey) return;
  const o = textObjs[selectedKey];
  if (o) canvas.remove(o);
  delete textObjs[selectedKey];
  delete state.layout.texts[selectedKey];
  delete state.dummy[selectedKey];
  const el = document.querySelector(`[data-dummy="${selectedKey}"]`);
  if (el) el.closest(".field").remove();
  selectedKey = null;
  $("#text-panel").hidden = true;
  canvas.requestRenderAll();
}

/* ----------------------------------------------------- fields & dummy -- */

function buildFieldsPanel() {
  const grid = $("#fields-grid");
  grid.innerHTML = "";
  for (const key of Object.keys(state.layout.texts)) {
    addFieldRow(key);
  }
}

function addFieldRow(key) {
  const grid = $("#fields-grid");
  const wrap = document.createElement("div");
  wrap.className = "field";

  const top = document.createElement("div");
  top.className = "field-top";
  const lbl = document.createElement("label");
  lbl.textContent = labelFor(key);
  lbl.title = key;
  const enable = document.createElement("input");
  enable.type = "checkbox";
  enable.checked = state.layout.texts[key].enabled !== false;
  enable.addEventListener("change", () => {
    const o = textObjs[key];
    if (o) o.visible = enable.checked;
    state.layout.texts[key].enabled = enable.checked;
    canvas && canvas.requestRenderAll();
  });
  top.append(lbl, enable);
  wrap.appendChild(top);

  const isLong = !!(state.layout.texts[key] && state.layout.texts[key].multiline);
  const input = document.createElement(isLong ? "textarea" : "input");
  if (!isLong) input.type = "text";
  input.value = state.dummy[key] || "";
  input.dataset.dummy = key;
  input.addEventListener("input", () => {
    state.dummy[key] = input.value;
    syncDummyToCanvas(key, input.value);
  });
  wrap.appendChild(input);
  grid.appendChild(wrap);
}

$("#btn-add-field").addEventListener("click", () => {
  const key = (prompt("Field key (e.g. blood_group):") || "").trim().replace(/\s+/g, "_");
  if (!key) return;
  if (state.layout.texts[key]) { alert("Field already exists: " + key); return; }
  state.layout.texts[key] = {
    x: 20, y: 40, width: 300, font_size: 30, min_font_size: 12,
    color: "#000000", bold: true, align: "left", multiline: false, uppercase: false,
    anchor: "la",
  };
  state.dummy[key] = "";
  addTextObj(key);
  addFieldRow(key);
  canvas.discardActiveObject();
  canvas.setActiveObject(textObjs[key]);
  onSelectionChanged();
  canvas.renderAll();
});

/* -------------------------------------------------------------- save --- */

function collectLayout() {
  if (photoObj) {
    syncPhotoPanel(); // reflect typed values back
    const sx = photoObj.scaleX || 1, sy = photoObj.scaleY || 1;
    state.layout.photo.x = Math.round(photoObj.left);
    state.layout.photo.y = Math.round(photoObj.top);
    state.layout.photo.width = Math.round(photoObj.width * sx);
    state.layout.photo.height = Math.round(photoObj.height * sy);
  }
  for (const key of Object.keys(textObjs)) {
    bakeTextObj(key, textObjs[key]);
  }
  const layout = {
    photo: { ...state.layout.photo },
    texts: {},
  };
  for (const key of Object.keys(textObjs)) {
    layout.texts[key] = { ...state.layout.texts[key] };
  }
  return layout;
}

async function saveTemplate() {
  const status = $("#save-status");
  status.textContent = "Saving…";
  status.classList.remove("ok");
  const body = { layout: collectLayout(), dummy: state.dummy };
  let res;
  try {
    res = await fetch(`/api/save/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    status.textContent = "Save failed (network)";
    return;
  }
  if (!res.ok) { status.textContent = "Save failed (HTTP " + res.status + ")"; return; }
  status.textContent = "Saved ✓ (will be used by next run)";
  status.classList.add("ok");
}

$("#btn-save").addEventListener("click", saveTemplate);

/* ----------------------------------------------------------- preview --- */

$("#btn-preview").addEventListener("click", async () => {
  await saveTemplate();
  const img = $("#preview-img");
  const modal = $("#preview-modal");
  $("#preview-missing").hidden = true;
  img.hidden = false;
  img.src = `/media/preview/${encodeURIComponent(name)}?t=${Date.now()}`;
  modal.hidden = false;
});
$("#preview-close").addEventListener("click", () => { $("#preview-modal").hidden = true; });

/* ------------------------------------------------------------------ go --- */

if (!FABRIC_CDN_OK) {
  document.body.insertAdjacentHTML(
    "beforeend",
    "<div style='position:fixed;inset:0;background:rgba(0,0,0,.85);color:#fff;display:flex;align-items:center;justify-content:center;padding:40px;text-align:center;font-family:sans-serif'>" +
    "The canvas library (Fabric.js) failed to load from the CDN. " +
    "This machine needs internet access to run the designer.</div>"
  );
} else {
  boot();
}
