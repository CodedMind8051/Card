/* Editor frontend: list cards, load one into a Fabric.js canvas, let the
 * user drag/resize text + the photo, crop/rotate the photo via Cropper.js,
 * and save back to the server (which re-renders the final PNG with PIL).
 *
 * Everything interactive happens client-side - no requests to the backend
 * until you hit "Save card" (or Ctrl+S).
 */

const FIELD_LABELS = {
  student_name: "Student name",
  section: "Section",
  father_name: "Father's name",
  mother_name: "Mother's name",
  class: "Class",
  roll_number: "Roll number",
  mobile_number: "Mobile number",
  dob: "Date of birth",
  address: "Address",
};

let canvas = null;
let currentName = null;
let currentLayout = null;
let currentData = null;
let sourceSize = null;
let photoObj = null;
let textObjs = {};   // fieldKey -> fabric.Textbox
let movedKeys = new Set();  // text keys the user has dragged/scaled
let fontBase = {};   // fieldKey -> base font size used for saving (updated on manual size change / scale)
let cropper = null;
let sourceImg = null;      // HTMLImageElement of the original upload
let sourceImgPromise = null;

let canvasW = 0;
let canvasH = 0;
let displayScale = 1;

// reference-panel (original photo) zoom
let refZoom = 1;        // user zoom multiplier, 1 = fit-to-panel
let refFitScale = 0;    // scale that fits the natural image into the panel

const $ = (sel) => document.querySelector(sel);

let allRecords = [];
let searchText = "";
let letterFilter = "All";
let sortMode = "name";

async function loadCardList() {
  let records;
  try {
    const res = await fetch("/api/records");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    records = await res.json();
  } catch (err) {
    console.error("Failed to load /api/records:", err);
    alert("Could not load the card list from the server. Check the terminal running --edit for errors.");
    return;
  }
  allRecords = records;
  renderCardList();
}

function matchesFilter(r) {
  const name = (r.student_name || "").toLowerCase();
  if (searchText && !name.includes(searchText.toLowerCase())) return false;
  if (letterFilter !== "All") {
    const first = (r.student_name || "").trim().charAt(0).toUpperCase();
    if (first !== letterFilter) return false;
  }
  return true;
}

function sortRecords(records) {
  const sorted = [...records];
  const byName = (a, b) => (a.student_name || "").localeCompare(b.student_name || "", undefined, { sensitivity: "base" });
  switch (sortMode) {
    case "name-desc":
      return sorted.sort((a, b) => byName(b, a));
    case "created":
      return sorted.sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
    case "updated":
      return sorted.sort((a, b) => (a.updated_at || 0) - (b.updated_at || 0));
    case "name":
    default:
      return sorted.sort(byName);
  }
}

function renderCardList() {
  const list = $("#card-list");
  list.innerHTML = "";
  const visible = sortRecords(allRecords.filter(matchesFilter));
  visible.forEach((r, i) => {
    const item = document.createElement("div");
    item.className = "card-item";
    item.dataset.name = r.name;
    item.innerHTML = `
      <span class="idx">${i + 1}</span>
      <img src="${r.output_url}" loading="lazy">
      <div class="meta">
        <div class="name">${r.student_name || "(no name)"}</div>
        <div class="file">${r.name}</div>
      </div>
      <button class="delete-btn" title="Delete card">✕</button>`;
    item.addEventListener("click", () => selectCard(r.name));
    item.querySelector(".delete-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteCard(r.name);
    });
    list.appendChild(item);
  });
  const countEl = $("#card-count");
  if (countEl) countEl.textContent = `${visible.length} / ${allRecords.length} cards`;
}

function buildLetterFilter() {
  const wrap = $("#letter-filter");
  wrap.innerHTML = "";
  const all = document.createElement("button");
  all.className = "letter-btn active";
  all.textContent = "All";
  all.dataset.letter = "All";
  all.addEventListener("click", () => setLetter("All"));
  wrap.appendChild(all);
  for (let c = 65; c <= 90; c++) {
    const ch = String.fromCharCode(c);
    const b = document.createElement("button");
    b.className = "letter-btn";
    b.textContent = ch;
    b.dataset.letter = ch;
    b.addEventListener("click", () => setLetter(ch));
    wrap.appendChild(b);
  }
}

function setLetter(l) {
  letterFilter = l;
  document.querySelectorAll(".letter-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.letter === l));
  renderCardList();
}

$("#card-search").addEventListener("input", (e) => {
  searchText = e.target.value;
  renderCardList();
});

$("#card-sort").addEventListener("change", (e) => {
  sortMode = e.target.value;
  renderCardList();
});

async function deleteCard(name) {
  if (!confirm(`Delete card "${name}"?\n\nThis removes the editable record and its rendered PNG from the output folder. The original photo is left untouched.`)) {
    return;
  }
  let res;
  try {
    res = await fetch(`/api/delete/${encodeURIComponent(name)}`, { method: "POST" });
  } catch (err) {
    console.error("delete request failed:", err);
    alert("Delete failed (network error).");
    return;
  }
  if (!res.ok) {
    console.error(`delete returned HTTP ${res.status}`);
    alert(`Delete failed (HTTP ${res.status}).`);
    return;
  }
  if (currentName === name) {
    currentName = null;
    currentLayout = null;
    currentData = null;
    photoObj = null;
    textObjs = {};
    $("#editor").hidden = true;
    $("#empty-state").hidden = false;
  }
  updateMoveButton();
  await loadCardList();
}

function updateMoveButton() {
  $("#btn-move").disabled = !currentName;
}

let movePath = null;
let moveParent = null;

$("#btn-move").addEventListener("click", async () => {
  if (!currentName) return;
  await moveLoadDirs("");                 // "" = project root on the server
  $("#move-modal").hidden = false;
});
$("#move-close").addEventListener("click", () => { $("#move-modal").hidden = true; });
$("#move-home").addEventListener("click", () => moveLoadDirs(""));
$("#move-up").addEventListener("click", () => { if (moveParent) moveLoadDirs(moveParent); });
$("#move-done").addEventListener("click", moveOutputImage);
$("#move-new").addEventListener("click", async () => {
  const name = (prompt("New folder name:") || "").trim();
  if (!name) return;
  const res = await fetch("/api/mkdir", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent: moveParent || "", name }),
  });
  if (!res.ok) { alert("Could not create the folder (HTTP " + res.status + ")."); return; }
  const out = await res.json();
  moveLoadDirs(out.path);
});

async function moveLoadDirs(path) {
  const res = await fetch(`/api/dirs?path=${encodeURIComponent(path || "")}`);
  if (!res.ok) { alert("Could not open that folder (HTTP " + res.status + ")."); return; }
  const data = await res.json();
  movePath = data.path;
  moveParent = data.parent;
  $("#move-path").textContent = data.path;
  const list = $("#move-dirs");
  list.innerHTML = "";
  if (!data.dirs.length) {
    list.innerHTML = '<div class="move-empty">No subfolders here</div>';
  }
  data.dirs.forEach((d) => {
    const el = document.createElement("button");
    el.className = "dir-row";
    el.innerHTML = `<span class="dir-icon">📁</span><span>${d}</span>`;
    el.addEventListener("click", () => moveLoadDirs(`${movePath}/${d}`));
    list.appendChild(el);
  });
}

async function moveOutputImage() {
  if (!currentName) return;
  if (!movePath) { alert("Pick a folder first."); return; }
  let res;
  try {
    res = await fetch(`/api/move/${encodeURIComponent(currentName)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: movePath }),
    });
  } catch (err) {
    console.error("move request failed:", err);
    alert("Move failed (network error).");
    return;
  }
  if (!res.ok) {
    let msg = `Move failed (HTTP ${res.status}).`;
    try { const dd = await res.json(); if (dd.description) msg = dd.description; } catch (e) { /* ignore */ }
    alert(msg);
    return;
  }
  const out = await res.json();
  $("#move-modal").hidden = true;
  $("#save-status").textContent = "Moved ✓";
  $("#save-status").classList.add("ok");
  const item = document.querySelector(`.card-item[data-name="${currentName}"] img`);
  if (item) item.src = out.output_url + "?t=" + Date.now();
}

async function selectCard(name) {
  document.querySelectorAll(".card-item").forEach(el => el.classList.toggle("active", el.dataset.name === name));

  let record;
  try {
    const res = await fetch(`/api/record/${encodeURIComponent(name)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    record = await res.json();
  } catch (err) {
    console.error(`Failed to load /api/record/${name}:`, err);
    alert(`Could not load "${name}" from the server. Check the terminal running --edit for the Python error.`);
    return;
  }

  currentName = name;
  currentLayout = record.layout;
  currentData = record.data;
  sourceSize = record.source_size;
  sourceImg = null;
  sourceImgPromise = null;
  updateMoveButton();

  $("#empty-state").hidden = true;
  $("#editor").hidden = false;

  const refImg = $("#reference-img");
  refZoom = 1;
  refFitScale = 0;
  refImg.style.width = "";
  refImg.style.maxWidth = "";
  refImg.style.maxHeight = "";
  $("#reference-panel .reference-body").classList.remove("zoomed");
  $("#ref-zoom-label").textContent = "100%";
  if (record.source_url) {
    refImg.hidden = false;
    refImg.src = record.source_url;
    $("#reference-missing").hidden = true;
    $("#btn-recrop-2").disabled = false;
  } else {
    refImg.hidden = true;
    $("#reference-missing").hidden = false;
    $("#btn-recrop-2").disabled = true;
    console.warn(`No original source image found on disk for "${name}". Recrop will be unavailable.`);
  }

  buildFieldsPanel();
  try {
    await buildCanvas(record);
  } catch (err) {
    console.error("Failed to build canvas:", err);
    alert("Something went wrong rendering the card on the canvas - check the browser console for details.");
  }
}

/* ------------------------------------------------------------ canvas --- */

function applyZoom() {
  canvas.setZoom(displayScale);
  const shell = $("#canvas-shell");
  shell.style.width = (canvasW * displayScale) + "px";
  shell.style.height = (canvasH * displayScale) + "px";
  $("#zoom-label").textContent = Math.round(displayScale * 100) + "%";
}

function setZoom(s) {
  displayScale = Math.min(3, Math.max(0.15, s));
  applyZoom();
}

async function buildCanvas(record) {
  if (canvas) { canvas.dispose(); canvas = null; }
  textObjs = {};
  movedKeys = new Set();
  fontBase = {};
  photoObj = null;

  const tpl = await loadImage(record.template_url);
  canvasW = tpl.width;
  canvasH = tpl.height;

  const el = $("#fabric-canvas");
  canvas = new fabric.Canvas(el, { selection: true });
  canvas.setWidth(canvasW);
  canvas.setHeight(canvasH);
  displayScale = Math.max(0.15, computeFitZoom());
  applyZoom();
  requestAnimationFrame(() => { displayScale = Math.max(0.15, computeFitZoom()); applyZoom(); });

  canvas.setBackgroundImage(tpl, canvas.renderAll.bind(canvas));

  // ---- text fields ----
  for (const [key, spec] of Object.entries(currentLayout.texts)) {
    const displayText = displayValueFor(key, spec);
    const tb = new fabric.Textbox(displayText || "", {
      left: spec.x, top: spec.y, width: spec.width || 400,
      fontSize: spec.font_size, fill: spec.color,
      fontFamily: "DejaVu Sans, Arial, sans-serif",
      fontWeight: spec.bold ? "bold" : "normal",
      textAlign: spec.align || "left",
      hasRotatingPoint: false,
      lockRotation: true,
    });
    tb.setControlsVisibility({ mtr: false });
    tb.fieldKey = key;
    tb.anchor = spec.anchor || "la";
    tb.on("changed", () => syncFieldInputFromCanvas(key));
    tb.on("editing:exited", () => { fitTextDisplay(key, tb); canvas.requestRenderAll(); });
    tb.on("modified", () => {
      // Fabric only emits 'moving'/'modified' (not 'moved'/'scaled'), so the
      // end-of-drag/end-of-scale is detected here. Bake a corner-scale into the
      // saved font size / width, or mark the key as moved so its position is
      // read back from the canvas object on save instead of the stored layout.
      const sx = tb.scaleX || 1, sy = tb.scaleY || 1;
      if (Math.abs(sx - 1) > 0.001 || Math.abs(sy - 1) > 0.001) {
        bakeScale(key, tb);
        return;
      }
      const spec = currentLayout.texts[key];
      const pt = anchorPointFromObject(key, tb);
      if (spec && (Math.abs(pt.x - spec.x) > 0.5 || Math.abs(pt.y - spec.y) > 0.5)) {
        movedKeys.add(key);
      }
    });
    tb.on("scaled", () => bakeScale(key, tb));
    canvas.add(tb);
    textObjs[key] = tb;
    fontBase[key] = spec.font_size;
    fitTextDisplay(key, tb);
  }

  // ---- photo (all client-side: crop + rotate happen in the browser) ----
  if (record.source_url && currentLayout.photo.crop) {
    try {
      await loadSourceImage(record.source_url);
      renderPhotoObject();
    } catch (err) {
      console.error("Could not load the original photo client-side:", err);
    }
  }

  canvas.on("selection:created", onSelectionChanged);
  canvas.on("selection:updated", onSelectionChanged);
  canvas.on("selection:cleared", () => { $("#text-controls").hidden = true; });

  canvas.requestRenderAll();
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    if (!url) { reject(new Error("empty image URL")); return; }
    fabric.Image.fromURL(url, (img) => {
      if (!img || !img.width) { reject(new Error("failed to load " + url)); return; }
      resolve(img);
    }, { crossOrigin: "anonymous" });
  });
}

function loadSourceImage(url) {
  if (sourceImgPromise) return sourceImgPromise;
  sourceImgPromise = new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => { sourceImg = img; resolve(img); };
    img.onerror = () => { sourceImg = null; sourceImgPromise = null; reject(new Error("source image failed: " + url)); };
    img.src = url;
  });
  return sourceImgPromise;
}

/* --------------------------------------------- reference photo zoom --- */

function computeRefFit() {
  const body = $("#reference-panel .reference-body");
  const img = $("#reference-img");
  if (!body || !img.naturalWidth) return 1;
  const availW = Math.max(60, body.clientWidth - 28);
  const availH = Math.max(60, body.clientHeight - 28);
  return Math.min(1, availW / img.naturalWidth, availH / img.naturalHeight);
}

function applyRefZoom() {
  const img = $("#reference-img");
  const body = $("#reference-panel .reference-body");
  if (!img.naturalWidth) return;
  if (refZoom <= 1.001) {
    img.style.width = "";
    img.style.maxWidth = "";
    img.style.maxHeight = "";
    body.classList.remove("zoomed");
    $("#ref-zoom-label").textContent = "100%";
    return;
  }
  const scale = refZoom * (refFitScale || 1);
  const dispW = Math.round(img.naturalWidth * scale);
  const dispH = Math.round(img.naturalHeight * scale);
  img.style.maxWidth = "none";
  img.style.maxHeight = "none";
  img.style.width = dispW + "px";
  img.style.height = "auto";
  // only lock to top-left once the image actually overflows the panel
  const availW = Math.max(60, body.clientWidth - 28);
  const availH = Math.max(60, body.clientHeight - 28);
  body.classList.toggle("zoomed", dispW > availW || dispH > availH);
  $("#ref-zoom-label").textContent = Math.round(refZoom * 100) + "%";
}

$("#reference-img").addEventListener("load", () => {
  refFitScale = computeRefFit();
  applyRefZoom();
});

$("#btn-ref-zoom-in").addEventListener("click", () => {
  refZoom = Math.min(8, Math.max(1, refZoom) * 1.25);
  applyRefZoom();
});
$("#btn-ref-zoom-out").addEventListener("click", () => {
  refZoom = Math.max(1, Math.min(8, refZoom) / 1.25);
  applyRefZoom();
});
$("#btn-ref-zoom-fit").addEventListener("click", () => { refZoom = 1; applyRefZoom(); });

// Mouse-wheel zoom over the original photo (no click-to-crop anymore).
$("#reference-panel .reference-body").addEventListener("wheel", (e) => {
  const img = $("#reference-img");
  if (!img.naturalWidth || img.hidden) return;
  e.preventDefault();
  const base = refZoom <= 1.001 ? 1 : refZoom;
  refZoom = Math.min(12, Math.max(1, base * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
  applyRefZoom();
}, { passive: false });
$("#reference-panel .reference-body").addEventListener("dblclick", () => { refZoom = 1; applyRefZoom(); });

/* -------------------------------------------------- photo rendering --- */

function roundedRectPath(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function renderCroppedPhoto(img, crop, rotation) {
  const x = Math.round(crop.x), y = Math.round(crop.y);
  const w = Math.max(1, Math.round(crop.w)), h = Math.max(1, Math.round(crop.h));

  const src = document.createElement("canvas");
  src.width = w; src.height = h;
  const sctx = src.getContext("2d");
  sctx.drawImage(img, x, y, w, h, 0, 0, w, h);

  if (!rotation) return src;

  const rad = rotation * Math.PI / 180;
  const cos = Math.abs(Math.cos(rad)), sin = Math.abs(Math.sin(rad));
  const nw = Math.ceil(w * cos + h * sin);
  const nh = Math.ceil(w * sin + h * cos);
  const out = document.createElement("canvas");
  out.width = nw; out.height = nh;
  const octx = out.getContext("2d");
  octx.fillStyle = "#fff";
  octx.fillRect(0, 0, nw, nh);
  octx.translate(nw / 2, nh / 2);
  octx.rotate(rad);
  octx.drawImage(src, -w / 2, -h / 2);
  return out;
}

function coverToBox(img, tw, th) {
  const scale = Math.max(tw / img.width, th / img.height);
  const dw = img.width * scale, dh = img.height * scale;
  const out = document.createElement("canvas");
  out.width = tw; out.height = th;
  const ctx = out.getContext("2d");
  ctx.drawImage(img, (tw - dw) / 2, (th - dh) / 2, dw, dh);
  return out;
}

function renderPhotoObject() {
  if (!canvas) return;
  const spec = currentLayout.photo;
  const crop = spec.crop;
  if (!sourceImg || !crop) return;

  let boxed = coverToBox(
    renderCroppedPhoto(sourceImg, crop, spec.rotation || 0),
    spec.width, spec.height
  );

  const radius = Math.max(0, spec.corner_radius || 20);
  if (radius > 0) {
    const rounded = document.createElement("canvas");
    rounded.width = spec.width; rounded.height = spec.height;
    const ctx = rounded.getContext("2d");
    roundedRectPath(ctx, 0, 0, spec.width, spec.height, radius);
    ctx.clip();
    ctx.drawImage(boxed, 0, 0);
    boxed = rounded;
  }

  const img = new fabric.Image(boxed);
  img.set({
    left: spec.x, top: spec.y,
    hasRotatingPoint: false,
    lockRotation: true,
    cornerColor: "#f0b455", cornerSize: 12, transparentCorners: false,
    borderColor: "#f0b455", borderScaleFactor: 2,
  });
  img.setControlsVisibility({ mtr: false });

  if (photoObj) {
    // preserve any drag/resize the user already did
    img.set({ left: photoObj.left, top: photoObj.top, scaleX: photoObj.scaleX, scaleY: photoObj.scaleY });
    canvas.remove(photoObj);
  }
  photoObj = img;
  canvas.add(photoObj);
  canvas.sendToBack(photoObj);
  canvas.requestRenderAll();
}

/* ------------------------------------------------------ text anchors --- */

// Layout text positions are PIL anchor points (matching fixedCard.py), not
// top-left corners. Convert between an anchor point and the fabric object's
// top-left so the on-screen preview and the saved coordinates agree.
function anchorFromKey(key) {
  const spec = currentLayout.texts[key];
  return spec ? (spec.anchor || "la") : "la";
}

function anchorPointFromObject(key, obj) {
  const anchor = anchorFromKey(key);
  const bw = obj.width * (obj.scaleX || 1);
  const bh = obj.height * (obj.scaleY || 1);
  return {
    x: anchor === "mm" ? obj.left + bw / 2 : obj.left,
    y: anchor === "la" ? obj.top : obj.top + bh / 2,
  };
}

function positionTextByAnchor(key, obj) {
  const spec = currentLayout.texts[key];
  if (!spec) return;
  const anchor = spec.anchor || "la";
  const bw = obj.width * (obj.scaleX || 1);
  const bh = obj.height * (obj.scaleY || 1);
  if (anchor === "mm") {
    obj.left = spec.x - bw / 2;
    obj.top = spec.y - bh / 2;
  } else if (anchor === "lm") {
    obj.left = spec.x;
    obj.top = spec.y - bh / 2;
  } else {
    obj.left = spec.x;
    obj.top = spec.y;
  }
}

function displayValueFor(key, spec) {
  let val = (currentData[key] || "");
  if (key === "student_name" && spec.uppercase) val = val.toUpperCase();
  if (key === "section" && val) val = (spec.prefix || "") + val;
  return val;
}

/* ------------------------------------------------------ text fitting --- */

// fixedCard.py shrinks text to fit its box instead of wrapping, so the
// browser preview must do the same or long values would wrap and overlap
// the next field. fitTextDisplay() mirrors the PIL shrink logic (single
// lines: -1pt until width fits; address: word-wrap, -2pt until height fits).

function fitFont() {
  const c = document.createElement("canvas").getContext("2d");
  fitFont.context = c;
  return c;
}

function fitMeasure(obj, text, size) {
  const c = fitFont();
  c.font = (obj.fontWeight === "bold" ? "bold " : "") + size + "px " + (obj.fontFamily || "DejaVu Sans");
  return c.measureText(text).width;
}

function fitWrapLines(obj, text, size, width) {
  const c = fitFont();
  c.font = (obj.fontWeight === "bold" ? "bold " : "") + size + "px " + (obj.fontFamily || "DejaVu Sans");
  const wrapped = [];
  for (const line of String(text).split("\n")) {
    let cur = "";
    for (const word of line.split(" ")) {
      const t = (cur + " " + word).trim();
      if (!cur || c.measureText(t).width <= width) {
        cur = t;
      } else {
        if (cur) wrapped.push(cur);
        cur = word;
      }
    }
    if (cur) wrapped.push(cur);
  }
  return wrapped;
}

function fitDisplaySize(key, obj, spec) {
  const base = fontBase[key] != null ? fontBase[key] : spec.font_size;
  const minSize = spec.min_font_size || 14;
  const width = spec.width || 500;

  if (spec.multiline) {
    const maxHeight = spec.height || 200;
    const c = fitFont();
    let size = base;
    while (size > minSize) {
      const lines = fitWrapLines(obj, obj.text || "", size, width);
      c.font = (obj.fontWeight === "bold" ? "bold " : "") + size + "px " + (obj.fontFamily || "DejaVu Sans");
      const m = c.measureText("Ay");
      const lineH = (m.actualBoundingBoxAscent || 0) + (m.actualBoundingBoxDescent || 0) + 4;
      if (lines.length * lineH <= maxHeight) break;
      size -= 2;
    }
    return size;
  }

  let size = base;
  while (size > minSize) {
    if (fitMeasure(obj, obj.text || "", size) <= width) break;
    size -= 1;
  }
  return size;
}

function fitTextDisplay(key, obj) {
  const spec = currentLayout.texts[key];
  if (!spec) return;
  const size = fitDisplaySize(key, obj, spec);
  if (obj.fontSize !== size) {
    obj.set("fontSize", size);
    obj.initDimensions && obj.initDimensions();
  }
  if (!movedKeys.has(key)) positionTextByAnchor(key, obj);
}

function bakeScale(key, obj) {
  // User corner-scaled a text box: fold the scale into the real size/width
  // so the saved layout stays clean and the anchor math stays valid.
  movedKeys.add(key);
  const sx = obj.scaleX || 1, sy = obj.scaleY || 1;
  obj.width = Math.max(1, obj.width * sx);
  obj.scaleX = 1;
  obj.scaleY = 1;
  fontBase[key] = Math.round(obj.fontSize * sy);
  obj.set("fontSize", fontBase[key]);
  obj.initDimensions && obj.initDimensions();
  fitTextDisplay(key, obj);
}

/* -------------------------------------------------------- fields form -- */

function buildFieldsPanel() {
  const grid = $("#fields-grid");
  grid.innerHTML = "";
  for (const key of Object.keys(currentLayout.texts)) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    const label = document.createElement("label");
    label.textContent = FIELD_LABELS[key] || key;
    wrap.appendChild(label);

    const isLong = key === "address";
    const input = document.createElement(isLong ? "textarea" : "input");
    if (!isLong) input.type = "text";
    input.value = currentData[key] || "";
    input.dataset.field = key;
    input.addEventListener("input", () => onFieldInput(key, input.value));
    wrap.appendChild(input);
    grid.appendChild(wrap);
  }
}

function onFieldInput(key, value) {
  currentData[key] = value;
  const spec = currentLayout.texts[key];
  const obj = textObjs[key];
  if (obj) {
    obj.text = displayValueFor(key, spec);
    fitTextDisplay(key, obj);
    canvas.requestRenderAll();
  }
}

function syncFieldInputFromCanvas(key) {
  const spec = currentLayout.texts[key];
  let val = textObjs[key].text || "";
  if (key === "section" && spec.prefix && val.startsWith(spec.prefix)) val = val.slice(spec.prefix.length);
  currentData[key] = val;
  const input = document.querySelector(`[data-field="${key}"]`);
  if (input) input.value = val;
}

/* -------------------------------------------------- selection toolbar -- */

function onSelectionChanged(e) {
  const obj = canvas.getActiveObject();
  if (!obj || !obj.fieldKey) { $("#text-controls").hidden = true; return; }
  $("#text-controls").hidden = false;
  $("#ctl-font-size").value = Math.round(fontBase[obj.fieldKey] != null ? fontBase[obj.fieldKey] : obj.fontSize);
  $("#ctl-color").value = rgbToHex(obj.fill);
  $("#ctl-bold").checked = obj.fontWeight === "bold";
}

$("#ctl-font-size").addEventListener("input", (e) => {
  const obj = canvas.getActiveObject();
  if (!obj || !obj.fieldKey) return;
  fontBase[obj.fieldKey] = parseInt(e.target.value || "10", 10);
  fitTextDisplay(obj.fieldKey, obj);
  canvas.requestRenderAll();
});
$("#ctl-color").addEventListener("input", (e) => {
  const obj = canvas.getActiveObject();
  if (!obj) return;
  obj.set("fill", e.target.value);
  canvas.requestRenderAll();
});
$("#ctl-bold").addEventListener("change", (e) => {
  const obj = canvas.getActiveObject();
  if (!obj) return;
  obj.set("fontWeight", e.target.checked ? "bold" : "normal");
  if (obj.fieldKey) fitTextDisplay(obj.fieldKey, obj);
  canvas.requestRenderAll();
});

function rgbToHex(v) {
  if (!v) return "#000000";
  if (v.startsWith("#")) return v;
  const m = v.match(/\d+/g);
  if (!m) return "#000000";
  return "#" + m.slice(0, 3).map(n => (+n).toString(16).padStart(2, "0")).join("");
}

/* -------------------------------------------------------- zoom -------- */

function computeFitZoom() {
  const area = document.querySelector(".canvas-area");
  const w = (area && area.clientWidth || 640) - 56;
  const h = (area && area.clientHeight || 600) - 56;
  if (!canvasW || !canvasH) return 1;
  return Math.min(1, w / canvasW, h / canvasH);
}

$("#btn-zoom-in").addEventListener("click", () => setZoom(displayScale * 1.2));
$("#btn-zoom-out").addEventListener("click", () => setZoom(displayScale / 1.2));
$("#btn-zoom-fit").addEventListener("click", () => setZoom(Math.max(0.15, computeFitZoom())));

/* -------------------------------------------------------- quick rotate - */

$("#btn-rotate-l").addEventListener("click", () => quickRotate(-90));
$("#btn-rotate-r").addEventListener("click", () => quickRotate(90));

function quickRotate(delta) {
  if (!currentLayout.photo.crop) return;
  currentLayout.photo.rotation = ((currentLayout.photo.rotation || 0) + delta + 360) % 360;
  renderPhotoObject();
}

/* ------------------------------------------------------------ cropping - */

$("#btn-recrop").addEventListener("click", openCropModal);
$("#btn-recrop-2").addEventListener("click", openCropModal);
$("#crop-close").addEventListener("click", closeCropModal);
$("#crop-reset").addEventListener("click", () => cropper && cropper.reset());
$("#crop-apply").addEventListener("click", applyCrop);
$("#crop-upload").addEventListener("click", () => $("#crop-file").click());
$("#crop-file").addEventListener("change", (e) => {
  const file = e.target.files && e.target.files[0];
  if (file) uploadNewPhoto(file);
  e.target.value = "";
});

// Map a crop rectangle picked on the output-derived photo (raw
// cropped+rotated, as it appears on the card) back into ORIGINAL source
// image pixel coordinates, so the backend renders the same region.
function mapCropToSourceRect(rect, crop, rotation) {
  const cw = Math.max(1, Math.round(crop.w)), ch = Math.max(1, Math.round(crop.h));
  const rad = (rotation || 0) * Math.PI / 180;
  const cos = Math.cos(rad), sin = Math.sin(rad);
  const nw = Math.ceil(cw * Math.abs(cos) + ch * Math.abs(sin));
  const nh = Math.ceil(cw * Math.abs(sin) + ch * Math.abs(cos));
  const pts = [
    [rect.x, rect.y],
    [rect.x + rect.width, rect.y],
    [rect.x + rect.width, rect.y + rect.height],
    [rect.x, rect.y + rect.height],
  ];
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const [X, Y] of pts) {
    const dx = X - nw / 2, dy = Y - nh / 2;
    const u = cw / 2 + dx * cos + dy * sin;
    const v = ch / 2 - dx * sin + dy * cos;
    minX = Math.min(minX, u); maxX = Math.max(maxX, u);
    minY = Math.min(minY, v); maxY = Math.max(maxY, v);
  }
  return {
    x: Math.round(crop.x + minX),
    y: Math.round(crop.y + minY),
    w: Math.round(maxX - minX),
    h: Math.round(maxY - minY),
  };
}

async function openCropModal() {
  const spec = currentLayout.photo;
  const crop = spec.crop;

  if (!crop) {
    alert("No photo crop has been set for this card yet, so there's nothing to crop. Save the card once first, then you can crop the photo shown on it.");
    return;
  }

  try {
    if (!sourceImg) {
      const url = $("#reference-img").src;
      if (!url) throw new Error("no source URL");
      await loadSourceImage(url);
    }
  } catch (err) {
    alert("The original uploaded photo couldn't be loaded, so there's nothing to crop. Check the Network tab for a failing /media/ request.");
    console.error("openCropModal source load failed:", err);
    return;
  }

  $("#crop-modal").hidden = false;
  showCropPreview();
}

function initCropperOn(img) {
  if (cropper) cropper.destroy();
  cropper = new Cropper(img, {
    viewMode: 1,
    autoCropArea: 1,
    background: false,
  });
}

function showCropPreview() {
  const img = $("#crop-image");
  let raw;
  try {
    raw = renderCroppedPhoto(sourceImg, currentLayout.photo.crop, currentLayout.photo.rotation || 0);
  } catch (err) {
    console.error("crop preview render failed:", err);
    return;
  }
  img.onload = () => initCropperOn(img);
  img.removeAttribute("src");
  img.src = raw.toDataURL("image/png");
}

async function uploadNewPhoto(file) {
  const fd = new FormData();
  fd.append("photo", file);

  let res;
  try {
    res = await fetch(`/api/upload_photo/${encodeURIComponent(currentName)}`, { method: "POST", body: fd });
  } catch (err) {
    console.error("photo upload failed:", err);
    alert("Upload failed (network error). Check the terminal running --edit.");
    return;
  }
  if (!res.ok) {
    alert("Upload failed (HTTP " + res.status + "). Check the terminal running --edit.");
    return;
  }
  const out = await res.json();

  // swap client-side state to the new photo and reset the crop to full image
  sourceImg = null;
  sourceImgPromise = null;
  try {
    await loadSourceImage(out.url);
  } catch (err) {
    console.error("uploaded photo could not be loaded:", err);
    alert("The uploaded photo couldn't be loaded.");
    return;
  }

  currentLayout.photo.crop = { x: 0, y: 0, w: out.width, h: out.height };
  currentLayout.photo.rotation = 0;

  // refresh the reference panel
  const ref = $("#reference-img");
  ref.hidden = false;
  ref.src = out.url;
  $("#reference-missing").hidden = true;
  $("#btn-recrop-2").disabled = false;

  renderPhotoObject();
  showCropPreview();

  const status = $("#save-status");
  status.textContent = "New photo loaded — crop it, then Save.";
  status.classList.remove("ok");
}

function closeCropModal() {
  $("#crop-modal").hidden = true;
  if (cropper) { cropper.destroy(); cropper = null; }
}

function applyCrop() {
  if (!cropper) return;
  const d = cropper.getData(true);
  const spec = currentLayout.photo;
  currentLayout.photo.crop = mapCropToSourceRect(d, spec.crop, spec.rotation || 0);
  closeCropModal();
  renderPhotoObject();
}

/* ----------------------------------------------------------------- save */

$("#btn-save").addEventListener("click", saveCard);
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    saveCard();
  }
});

// Move the selected object with the arrow keys (Shift = 10px, else 1px).
// Works on both text fields and the photo box, and the move is saved.
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
  if (obj.fieldKey) movedKeys.add(obj.fieldKey);
  canvas.requestRenderAll();
});

function collectLayoutFromCanvas() {
  const layout = { photo: { ...currentLayout.photo }, texts: {} };

  if (photoObj) {
    layout.photo.x = Math.round(photoObj.left);
    layout.photo.y = Math.round(photoObj.top);
    layout.photo.width = Math.round(photoObj.width * photoObj.scaleX);
    layout.photo.height = Math.round(photoObj.height * photoObj.scaleY);
  }

  for (const [key, obj] of Object.entries(textObjs)) {
    const spec = currentLayout.texts[key];
    let x = spec.x, y = spec.y;
    if (movedKeys.has(key)) {
      const pt = anchorPointFromObject(key, obj);
      x = pt.x;
      y = pt.y;
    }
    layout.texts[key] = {
      ...spec,
      x: Math.round(x),
      y: Math.round(y),
      width: Math.round(obj.width * (obj.scaleX || 1)),
      font_size: Math.round((fontBase[key] != null ? fontBase[key] : spec.font_size) * (obj.scaleY || 1)),
      color: rgbToHex(obj.fill),
      bold: obj.fontWeight === "bold",
    };
  }
  return layout;
}

async function saveCard() {
  const status = $("#save-status");
  status.textContent = "Saving…";
  status.classList.remove("ok");

  const layout = collectLayoutFromCanvas();
  let res;
  try {
    res = await fetch(`/api/save/${encodeURIComponent(currentName)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data: currentData, layout }),
    });
  } catch (err) {
    console.error("save request failed:", err);
    status.textContent = "Save failed (network)";
    return;
  }

  if (!res.ok) {
    console.error(`save returned HTTP ${res.status}`);
    status.textContent = `Save failed (HTTP ${res.status})`;
    return;
  }
  const out = await res.json();
  status.textContent = "Saved ✓";
  status.classList.add("ok");
  currentLayout = layout;

  // update the thumbnail in the sidebar
  const item = document.querySelector(`.card-item[data-name="${currentName}"] img`);
  if (item) item.src = out.output_url + "?t=" + Date.now();
}

/* ------------------------------------------------------- collapsible -- */

const COLLAPSIBLE = [
  { btn: "#btn-collapse-toolbar", target: ".toolbar", key: "ui.toolbar" },
  { btn: "#btn-collapse-ref", target: "#reference-panel", key: "ui.ref" },
  { btn: "#btn-collapse-fields", target: "#fields-panel", key: "ui.fields" },
];

for (const { btn, target, key } of COLLAPSIBLE) {
  const el = document.querySelector(target);
  const button = document.querySelector(btn);
  const apply = (collapsed) => el.classList.toggle("collapsed", collapsed);
  try { apply(localStorage.getItem(key) === "1"); } catch (e) { /* storage disabled */ }
  button.addEventListener("click", () => {
    const collapsed = !el.classList.contains("collapsed");
    apply(collapsed);
    try { localStorage.setItem(key, collapsed ? "1" : "0"); } catch (e) { /* ignore */ }
  });
}

/* ------------------------------------------------------------------ go - */

if (typeof fabric === "undefined" || typeof Cropper === "undefined") {
  alert(
    "The editor's canvas library failed to load from the CDN (cdnjs.cloudflare.com). " +
    "This usually means the machine running the browser has no internet access. " +
    "The scraper itself doesn't need internet for --edit, but the page currently does, " +
    "since it loads Fabric.js/Cropper.js from a CDN."
  );
} else {
  buildLetterFilter();
  loadCardList();
}
