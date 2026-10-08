// Aircraft silhouettes, drawn once at startup and handed to MapLibre as SDF
// icons so a single image per shape can be recoloured (by altitude) and
// outlined (halo) on the GPU.
//
// Shapes are drawn nose-up in a 64px box centred on (0, 0); MapLibre rotates
// them to each aircraft's track.

const SIZE = 80;      // canvas size, including padding for the distance field
const RADIUS = 8;     // SDF spread in pixels (matches MapLibre's SDF_PX)
const CUTOFF = 0.25;  // MapLibre treats 0.75 as the edge

// ---------------------------------------------------------------- drawing

function mirrored(ctx, pts) {
  // pts: right half, from the centreline back to the centreline.
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (const [x, y] of pts.slice(1)) ctx.lineTo(x, y);
  for (const [x, y] of pts.slice().reverse()) ctx.lineTo(-x, y);
  ctx.closePath();
  ctx.fill();
}

function capsule(ctx, x, y1, y2, w) {
  const r = w / 2;
  ctx.beginPath();
  ctx.moveTo(x - r, y1 + r);
  ctx.arc(x, y1 + r, r, Math.PI, 0);
  ctx.lineTo(x + r, y2 - r);
  ctx.arc(x, y2 - r, r, 0, Math.PI);
  ctx.closePath();
  ctx.fill();
}

function ellipse(ctx, x, y, rx, ry) {
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  ctx.fill();
}

function pair(fn) {
  // Draw something on both sides of the centreline.
  return (ctx, x, ...rest) => { fn(ctx, x, ...rest); fn(ctx, -x, ...rest); };
}
const engines = pair((ctx, x, y, w, h) => capsule(ctx, x, y, y + h, w));

function jet(ctx, { len, body, span, wingY, sweep, root, tip, tailSpan, tailY, eng = [], rear = false, tTail = false }) {
  capsule(ctx, 0, -len / 2, len / 2, body);
  mirrored(ctx, [[0, wingY], [span, wingY + sweep], [span, wingY + sweep + tip], [0, wingY + root]]);
  const ty = tTail ? tailY + 1 : tailY;
  mirrored(ctx, [[0, ty], [tailSpan, ty + 5], [tailSpan, ty + 8], [0, ty + 7]]);
  for (const [x, y, w, h] of eng) engines(ctx, x, y, w, h);
  if (rear) engines(ctx, body / 2 + 2, len / 2 - 20, 4.5, 9);
}

function straightWing(ctx, { len, body, span, wingY, chord, tailSpan, nacelles = [], noseProp = false }) {
  capsule(ctx, 0, -len / 2, len / 2, body);
  mirrored(ctx, [[0, wingY], [span, wingY + 1], [span, wingY + chord - 1], [0, wingY + chord]]);
  mirrored(ctx, [[0, len / 2 - 8], [tailSpan, len / 2 - 6], [tailSpan, len / 2 - 3], [0, len / 2 - 2]]);
  for (const x of nacelles) {
    engines(ctx, x, wingY - 6, 3.5, chord + 8);
    ctx.fillRect(x - 6, wingY - 7.5, 12, 1.6);
    ctx.fillRect(-x - 6, wingY - 7.5, 12, 1.6);
  }
  if (noseProp) ctx.fillRect(-6, -len / 2 - 1.5, 12, 1.6);
}

const SHAPES = {
  narrow: (c) => jet(c, { len: 52, body: 6, span: 25, wingY: -5, sweep: 12, root: 13, tip: 3.5,
    tailSpan: 10, tailY: 17, eng: [[9.5, -2, 3.8, 8]] }),
  heavy2: (c) => jet(c, { len: 58, body: 7.5, span: 29, wingY: -7, sweep: 14, root: 15, tip: 4,
    tailSpan: 12, tailY: 19, eng: [[11, -4, 4.8, 10]] }),
  heavy4: (c) => jet(c, { len: 60, body: 8, span: 30, wingY: -8, sweep: 15, root: 16, tip: 4,
    tailSpan: 12, tailY: 20, eng: [[10, -5, 4.2, 9], [19, 0, 4, 8]] }),
  regional: (c) => jet(c, { len: 50, body: 5.5, span: 21, wingY: -2, sweep: 7, root: 10, tip: 3,
    tailSpan: 9, tailY: 18, rear: true, tTail: true }),
  bizjet: (c) => jet(c, { len: 46, body: 5, span: 19, wingY: 0, sweep: 7, root: 9, tip: 2.5,
    tailSpan: 8, tailY: 15, rear: true, tTail: true }),
  turboprop: (c) => straightWing(c, { len: 50, body: 5.5, span: 26, wingY: -4, chord: 6,
    tailSpan: 9, nacelles: [8.5] }),
  turboprop4: (c) => straightWing(c, { len: 54, body: 7, span: 29, wingY: -6, chord: 7,
    tailSpan: 11, nacelles: [9, 18] }),
  light: (c) => straightWing(c, { len: 40, body: 5, span: 24, wingY: -9, chord: 6,
    tailSpan: 8, noseProp: true }),
  twin: (c) => straightWing(c, { len: 42, body: 5, span: 24, wingY: -7, chord: 6,
    tailSpan: 8, nacelles: [8] }),
  heli: (c) => {
    c.lineWidth = 2.2;
    c.beginPath(); c.arc(0, -4, 22, 0, Math.PI * 2); c.stroke();
    ellipse(c, 0, -4, 6, 11);
    c.fillRect(-1.3, 4, 2.6, 22);
    c.fillRect(-6, 24, 12, 2.2);
  },
  fighter: (c) => {
    mirrored(c, [[0, -30], [2.5, -18], [4, -6], [20, 12], [20, 16], [5, 16], [9, 26], [9, 28], [0, 26]]);
  },
  glider: (c) => {
    capsule(c, 0, -22, 22, 4);
    mirrored(c, [[0, -6], [31, -5], [31, -2.5], [0, -2]]);
    mirrored(c, [[0, 17], [8, 18], [8, 20], [0, 20.5]]);
  },
  uav: (c) => {
    capsule(c, 0, -16, 18, 4.5);
    mirrored(c, [[0, -3], [28, -1], [28, 2], [0, 3]]);
    mirrored(c, [[0, 14], [7, 18], [7, 20], [0, 19]]);
  },
  balloon: (c) => { ellipse(c, 0, 0, 16, 16); },
  ground: (c) => { c.fillRect(-8, -12, 16, 24); },
};

// Relative on-screen size of each shape.
export const SHAPE_SCALE = {
  heavy4: 1.0, heavy2: 0.92, narrow: 0.78, regional: 0.7, bizjet: 0.62, turboprop: 0.7,
  turboprop4: 0.85, light: 0.58, twin: 0.62, heli: 0.62, fighter: 0.7, glider: 0.62,
  uav: 0.55, balloon: 0.5, ground: 0.42,
};

// ---------------------------------------------------------------- SDF

function toSDF(ctx) {
  const { data } = ctx.getImageData(0, 0, SIZE, SIZE);
  const inside = new Uint8Array(SIZE * SIZE);
  for (let i = 0; i < inside.length; i++) inside[i] = data[i * 4 + 3] > 127 ? 1 : 0;
  const out = new Uint8ClampedArray(SIZE * SIZE * 4);
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const me = inside[y * SIZE + x];
      let best = RADIUS + 1;
      for (let dy = -RADIUS; dy <= RADIUS; dy++) {
        const yy = y + dy;
        if (yy < 0 || yy >= SIZE) continue;
        for (let dx = -RADIUS; dx <= RADIUS; dx++) {
          const xx = x + dx;
          if (xx < 0 || xx >= SIZE || inside[yy * SIZE + xx] === me) continue;
          const d = Math.hypot(dx, dy);
          if (d < best) best = d;
        }
      }
      const signed = me ? -(best - 0.5) : best - 0.5; // negative inside
      const v = 255 - 255 * (signed / RADIUS + CUTOFF);
      const o = (y * SIZE + x) * 4;
      out[o] = out[o + 1] = out[o + 2] = 255;
      out[o + 3] = v;
    }
  }
  return { width: SIZE, height: SIZE, data: out };
}

export function addAircraftIcons(map) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = SIZE;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  for (const [name, draw] of Object.entries(SHAPES)) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, SIZE, SIZE);
    ctx.translate(SIZE / 2, SIZE / 2);
    ctx.fillStyle = ctx.strokeStyle = "#fff";
    draw(ctx);
    map.addImage(`ac-${name}`, toSDF(ctx), { sdf: true, pixelRatio: 2 });
  }
}

// ---------------------------------------------------------------- type → shape

const TYPES = {
  heavy4: "A388 A380 A342 A343 A345 A346 B741 B742 B743 B744 B748 B74S B74R BLCF IL96 IL76 A124 A225 C17 C5 C5M K35R K35E B703 E3TF E3CF B52 E6 VC25",
  heavy2: "A306 A30B A310 A332 A333 A337 A338 A339 A359 A35K B762 B763 B764 B772 B773 B77L B77W B778 B779 B788 B789 B78X MD11 DC10 L101 KC10 K46 KC46 A3ST",
  narrow: "A318 A319 A320 A321 A19N A20N A21N B731 B732 B733 B734 B735 B736 B737 B738 B739 B37M B38M B39M B3XM B752 B753 B712 B721 B722 MD81 MD82 MD83 MD87 MD88 MD90 E170 E75L E75S E190 E195 E290 E295 BCS1 BCS3 C919 A148 SU95 P8 E737",
  regional: "CRJ1 CRJ2 CRJ7 CRJ9 CRJX E135 E145 E35L F70 F100 DC93 DC95 B461 B462 B463 RJ70 RJ85 RJ1H",
  bizjet: "GLF4 GLF5 GLF6 GA5C GA6C GA7C G280 G150 GALX GLEX GL5T GL6T GL7T C25A C25B C25C C25M C500 C501 C510 C525 C550 C560 C56X C650 C680 C68A C700 C750 CL30 CL35 CL60 E50P E55P E545 E550 FA7X FA8X F2TH F900 FA10 FA20 FA50 FA6X H25B H25C LJ31 LJ35 LJ40 LJ45 LJ55 LJ60 LJ70 LJ75 PC24 HDJT PRM1 BE40 SF50 EA50 ASTR WW24",
  turboprop: "AT43 AT44 AT45 AT46 AT72 AT73 AT75 AT76 DH8A DH8B DH8C DH8D B190 B350 BE20 BE30 BE9L BE9T BE99 SF34 SW3 SW4 D328 JS31 JS32 JS41 E110 E120 DHC6 C212 CN35 C295 F50 SB20 L410 AN26 MA60 P180",
  turboprop4: "C130 C30J A400 P3 L188 DHC7 AN12 E2",
  twin: "BE55 BE56 BE58 BE60 BE76 PA23 PA27 PA30 PA31 PA34 PA44 P68 DA42 DA62 C310 C320 C335 C340 C402 C404 C414 C421 AC50 AEST",
  light: "C150 C152 C162 C170 C172 C175 C177 C180 C182 C185 C195 C205 C206 C207 C208 C210 P210 P28A P28B P28R P28T P32R P32T PA18 PA20 PA22 PA24 PA28 PA32 PA46 P46T PA38 SR20 SR22 S22T DA20 DA40 DA50 M20P M20T M20J BE33 BE35 BE36 BE23 BE24 AA1 AA5 RV4 RV6 RV7 RV8 RV9 RV10 RV12 RV14 PC12 PC6 PC7 PC9 PC21 TBM7 TBM8 TBM9 KODI T6 TEX2 C77R C82R CH60 J3 CUB GLAS EVOT DV20 TOBA",
  heli: "H60 S70 S76 S92 S61 EC20 EC25 EC30 EC35 EC45 EC55 EC75 AS32 AS50 AS55 AS65 B06 B06T B105 B212 B222 B230 B407 B412 B427 B429 B430 B47G B505 R22 R44 R66 A109 A119 A139 A149 A169 A189 H47 CH47 H53 H53S H64 UH1 UH1Y AH1 MD52 MD60 MD90 NH90 H125 H130 H135 H145 H155 H160 H175 H215 H225 EH10 V22 BK17 G2CA",
  fighter: "F16 F15 F18 F18S FA18 F35 F22 F14 F4 F5 A10 T38 T45 EUFI RFAL TOR HAWK MIG29 SU27 SU30 SU35 J10 F117 B1 B2 GRIF",
  glider: "GLID ASK21 ASK13 DG1T DISC LS8 ASW2 ARCP DUOD JANU NIMB",
  balloon: "BALL",
  uav: "Q9 Q4 RQ4 MQ9 HRON",
};

const TYPE_SHAPE = new Map();
for (const [shape, list] of Object.entries(TYPES)) {
  for (const t of list.split(/\s+/)) TYPE_SHAPE.set(t, shape);
}

// ADS-B emitter category, for types we don't know.
const CATEGORY_SHAPE = {
  A1: "light", A2: "bizjet", A3: "narrow", A4: "narrow", A5: "heavy2", A6: "fighter",
  A7: "heli", B1: "glider", B2: "balloon", B4: "light", B6: "uav",
  C1: "ground", C2: "ground", C3: "ground",
};

export function shapeFor(type, category) {
  return TYPE_SHAPE.get(type) || CATEGORY_SHAPE[category] || "narrow";
}
