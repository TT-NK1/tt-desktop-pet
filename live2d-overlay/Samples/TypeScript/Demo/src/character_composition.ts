// Measurements use the 330 × 390 desktop canvas at the 100% size preset.
// Landmarks are on the body and shoes, excluding the silhouette of props and clothing.
const originX = 166.16;
const originY = 214.72;
const pixelsPerUnit = 132;
const unityLine = 180;

export const frameReference = {
  width: 330, height: 390, center: 172, crown: 83, shoulder: 174,
  chest: 193, knee: 211, sole: 341,
  safe: [42, 45, 294, 353],
} as const;

type Landmarks = {
  knee: number;
  sole: number;
  kneeTarget: number;
  centerShift: number;
  kneeWidth: number;
};

export const poseComposition: Record<string, Landmarks> = {
  Reading:       { knee: 225, sole: 343, kneeTarget: 214, centerShift: -1, kneeWidth: .99 },
  ListeningMusic:{ knee: 207, sole: 339, kneeTarget: 207, centerShift:  0, kneeWidth: 1 },
  gothic:        { knee: 207, sole: 343, kneeTarget: 207, centerShift:  0, kneeWidth: 1 },
  tea:           { knee: 214, sole: 357, kneeTarget: 210, centerShift:  1, kneeWidth: .98 },
  handcraft:     { knee: 218, sole: 358, kneeTarget: 212, centerShift:  0, kneeWidth: .98 },
  maid:          { knee: 207, sole: 343, kneeTarget: 207, centerShift:  0, kneeWidth: 1 },
  serving:       { knee: 218, sole: 359, kneeTarget: 212, centerShift:  1, kneeWidth: .98 },
  cleaning:      { knee: 211, sole: 359, kneeTarget: 210, centerShift:  1, kneeWidth: .98 },
  bunny:         { knee: 210, sole: 348, kneeTarget: 211, centerShift:  1, kneeWidth: 1 },
  hug:           { knee: 217, sole: 359, kneeTarget: 214, centerShift:  0, kneeWidth: .98 },
  stretch:       { knee: 224, sole: 379, kneeTarget: 215, centerShift:  0, kneeWidth: .94 },
  sailor:        { knee: 207, sole: 343, kneeTarget: 207, centerShift:  0, kneeWidth: 1 },
  phone:         { knee: 211, sole: 359, kneeTarget: 210, centerShift:  1, kneeWidth: .98 },
  photo:         { knee: 211, sole: 359, kneeTarget: 210, centerShift:  1, kneeWidth: .98 },
};

function smoothProgress(start: number, end: number, y: number): number {
  const t = Math.max(0, Math.min(1, (y - start) / (end - start)));
  return t * t * (3 - 2 * t);
}

export function bodyComposition(name: string) {
  const row = poseComposition[name];
  if (!row) return null;
  return {
    ...row,
    // Translate the shoes together with their cuffs. Keep shoe shape and sole angle.
    footStart: row.sole - 31,
    kneeOffset: row.kneeTarget - row.knee,
    footOffset: frameReference.sole - row.sole,
  };
}

export function composeBodyVertex(points: Float32Array, index: number, field: ReturnType<typeof bodyComposition>): void {
  if (!field) return;
  const y = originY - points[index + 1] * pixelsPerUnit;
  if (y <= unityLine) return;
  const upper = smoothProgress(unityLine, field.knee, y);
  const lower = smoothProgress(field.knee, field.footStart, y);
  const dy = field.kneeOffset * upper + (field.footOffset - field.kneeOffset) * lower;
  const kneeWeight = upper * (1 - lower);
  const center = (frameReference.center - originX) / pixelsPerUnit;
  points[index] += (points[index] - center) * (field.kneeWidth - 1) * kneeWeight
    + field.centerShift * upper / pixelsPerUnit;
  points[index + 1] -= dy / pixelsPerUnit;
}
