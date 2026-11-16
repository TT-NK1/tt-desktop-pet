/** Face artwork in the original model's 1199 × 1312 pixel coordinate space. */
export const faceRect = { x: 382, y: 184, width: 294, height: 248 };

export type FacePose = {
  gazeX: number;
  gazeY: number;
  leftOpen: number;
  rightOpen: number;
  mouth: number;
};

export function smooth(low: number, high: number, value: number): number {
  const t = Math.max(0, Math.min(1, (value - low) / (high - low)));
  return t * t * (3 - 2 * t);
}

const eyeRegions = [
  { x: 480, y: 343, width: 44, height: 35, horizontal: 6.2, vertical: 4.2 },
  { x: 584, y: 264, width: 45, height: 36, horizontal: 6.8, vertical: 4.6 },
];

type Sample = [number, number, number];
const { x: left, y: top, width, height } = faceRect;

function sample(plate: Uint8ClampedArray, x: number, y: number, out: Sample): void {
  x = Math.max(0, Math.min(width - 1.001, x - left - .5));
  y = Math.max(0, Math.min(height - 1.001, y - top - .5));
  const ix = Math.floor(x), iy = Math.floor(y);
  const a = (iy * width + ix) * 4;
  const fx = x - ix, fy = y - iy;
  for (let c = 0; c < 3; c++) {
    const i = a + c, j = i + width * 4;
    out[c] = (plate[i] * (1 - fx) + plate[i + 4] * fx) * (1 - fy)
      + (plate[j] * (1 - fx) + plate[j + 4] * fx) * fy;
  }
}

type EyePixel = { index: number; x: number; y: number; weight: number; socket: number; blink: number };
type MouthPixel = { index: number; u: number; v: number; weight: number };

export class FacePixels {
  readonly rgba: Uint8Array;
  private readonly base: Uint8Array;
  private readonly eyes: EyePixel[][] = [[], []];
  private readonly mouth: MouthPixel[] = [];
  private readonly rgb: Sample = [0, 0, 0];
  // Tangent and normal to the original tilted lip line.
  private readonly cos = .934;
  private readonly sin = -.357;

  constructor(
    private readonly open: Uint8ClampedArray,
    private readonly closed: Uint8ClampedArray,
    private readonly skin: Uint8ClampedArray,
  ) {
    this.base = new Uint8Array(open.length);
    this.rgba = new Uint8Array(open.length);
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const index = (y * width + x) * 4;
        // Broad fade over continuous pixels. Keep the cuts and the features in the opaque core.
        const edge = Math.min(x, width - 1 - x, y, height - 1 - y);
        const alpha = open[index + 3] * smooth(1, 22, edge);
        this.base[index + 3] = Math.round(alpha);
        for (let c = 0; c < 3; c++) this.base[index + c] = Math.round(open[index + c] * alpha / 255);
        if (alpha <= 0) continue;
        const ax = left + x + .5, ay = top + y + .5;
        eyeRegions.forEach((eye, side) => {
          const dx = ax - eye.x, dy = ay - eye.y;
          const radius = (dx / eye.width) ** 2 + (dy / eye.height) ** 2;
          const blink = this.closed[index] !== this.open[index]
            || this.closed[index + 1] !== this.open[index + 1]
            || this.closed[index + 2] !== this.open[index + 2];
          const insideBlink = (side === 0 ? ax >= 422 && ax < 527 && ay >= 295 && ay < 380
            : ax >= 529 && ax < 632 && ay >= 220 && ay < 310) && blink;
          if (radius >= 1 && !insideBlink) return;
          const weight = 1 - smooth(.19, 1, radius);
          // Fade the gaze motion before reaching the bangs or the corner of the socket.
          const fringe = side === 0 ? smooth(316, 341, ay) : smooth(231, 251, ay);
          const visibleSkin = smooth(109, 190, this.skin[index]);
          this.eyes[side].push({ index, x: ax, y: ay, weight: weight * fringe * visibleSkin,
            socket: weight * (.46 + .54 * fringe) * visibleSkin, blink: insideBlink ? 1 : 0 });
        });
        const dx = ax - 582.5, dy = ay - 383;
        const u = dx * this.cos + dy * this.sin, v = -dx * this.sin + dy * this.cos;
        const radius = (u / 21) ** 2 + (v / 14) ** 2;
        if (radius < 1) this.mouth.push({ index, u, v, weight: 1 - smooth(.53, 1, radius) });
      }
    }
  }

  render(pose: FacePose): Uint8Array {
    this.rgba.set(this.base);
    eyeRegions.forEach((eye, side) => {
      const openness = side === 0 ? pose.leftOpen : pose.rightOpen;
      const closure = 1 - smooth(.05, .94, openness);
      const finishBlink = smooth(.78, .99, closure);
      for (const pixel of this.eyes[side]) {
        // Both eyes follow one direction. The larger eye travels slightly farther in pixels.
        const weight = pixel.weight * (1 - finishBlink);
        const socket = pixel.socket * (1 - finishBlink);
        const normal = (pixel.x - eye.x) * .32 + (pixel.y - eye.y) * .947;
        const squeeze = .90 * closure * socket;
        const eyelid = Math.max(-12, Math.min(12, (normal * squeeze - 2.6 * closure * socket) / (1 - squeeze)));
        const sx = pixel.x - pose.gazeX * eye.horizontal * weight + .32 * eyelid;
        const sy = pixel.y + pose.gazeY * eye.vertical * weight + .947 * eyelid;
        sample(this.open, sx, sy, this.rgb);
        const blink = finishBlink * pixel.blink;
        this.write(pixel.index, this.rgb, this.closed, blink);
      }
    });

    const opening = pose.mouth;
    const widthScale = .92 + .08 * opening;
    const heightScale = .24 + 1.00 * opening;
    for (const { index, u, v, weight } of this.mouth) {
      const sx = u / widthScale, sy = v / heightScale;
      sample(this.open, 582.5 + sx * this.cos - sy * this.sin, 383 + sx * this.sin + sy * this.cos, this.rgb);
      const mouthMask = 1 - smooth(.64, 1, (sx / 16.5) ** 2 + (sy / 9.4) ** 2);
      const oral = mouthMask * smooth(.07, .40, opening);
      const lipY = -.85 + .012 * u * u;
      const lip = (1 - smooth(.28, 1.35, Math.abs(v - lipY)))
        * (1 - smooth(6.4, 10.2, Math.abs(u))) * (1 - smooth(.04, .32, opening)) * .77;
      const alpha = this.base[index + 3] / 255;
      for (let c = 0; c < 3; c++) {
        const skin = this.skin[index + c];
        const mouth = skin + oral * (this.rgb[c] - skin);
        const line = mouth + lip * ([104, 61, 64][c] - mouth);
        this.rgba[index + c] = Math.round((this.open[index + c] + weight * (line - this.open[index + c])) * alpha);
      }
    }
    return this.rgba;
  }

  private write(index: number, rgb: Sample, other: Uint8ClampedArray, weight: number): void {
    const alpha = this.base[index + 3] / 255;
    for (let c = 0; c < 3; c++) {
      this.rgba[index + c] = Math.round((rgb[c] + weight * (other[index + c] - rgb[c])) * alpha);
    }
  }
}


