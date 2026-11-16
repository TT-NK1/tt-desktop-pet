import { ease, PoseGesture } from './street_pose_gestures';

const teaDuration = 3.3;
export const sweepDuration = 4.4;
const grip: [number, number] = [323, 467];
const dustCount = 5;

export type MaidFrame = {
  offer: number;
  elapsed: number | null;
  sweep: number;
  dust: {x: number; y: number; size: number; alpha: number}[];
};

export class MaidMotion {
  private readonly serve = new PoseGesture({interval: [20, 45], duration: teaDuration});
  private nextDust = 1;
  private dustCycle = -1;
  private particles: {x: number; y: number; size: number; life: number; drift: number; delay: number}[] = [];

  constructor(private readonly name: string) {}

  reset(firstDelay?: number) {
    this.serve.reset(firstDelay);
    this.nextDust = 1 + Math.floor(Math.random() * 2);
    this.dustCycle = -1;
    this.particles = [];
  }

  sample(time: number, breath: number): MaidFrame {
    const offer = this.name === 'serving' ? this.serve.advance(time) : null;
    const e = offer?.elapsed ?? 0;
    const strength = ease((e - .15) / 1.02) * (1 - ease((e - 1.9) / 1.4));
    const frame: MaidFrame = {offer: strength, elapsed: offer?.elapsed ?? null, sweep: 0, dust: []};
    if (this.name !== 'cleaning') return frame;
    const cycle = Math.floor(time / sweepDuration);
    const at = time % sweepDuration;
    frame.sweep = sweepAt(at);
    if (cycle >= this.nextDust && at >= 1.2) {
      this.dustCycle = cycle;
      this.nextDust = cycle + 2 + Math.floor(Math.random() * 3);
      const [x, y] = cleanPoint(213, 354, sweepAt(1.2), breath);
      this.particles = Array.from({length: 2 + Math.floor(Math.random() * (dustCount-1))}, (_, i) => ({
        x: x - 9 - 9 * i + 6 * Math.random(), y: y + 14 + 6 * i,
        size: 4 + Math.random() * 4, life: .85 + Math.random() * .38,
        drift: -13 - Math.random() * 17, delay: .045 * i,
      }));
    }
    if (cycle === this.dustCycle) frame.dust = this.particles.map(p => {
      const age = Math.max(0, at - 1.2 - p.delay);
      return {x: p.x + p.drift * age, y: p.y + 17 * age + 29 * age * age,
        size: p.size, alpha: .46 * ease(age / .17) * (1 - ease((age - .21) / (p.life - .21)))};
    });
    return frame;
  }
}

function sweepAt(at: number) {
  if (at < 1.18) return -ease((at - .30) / .88);
  if (at < 2.18) return -1 + ease((at - 1.18) / 1);
  if (at < 3.12) return ease((at - 2.18) / .94);
  return 1 - ease((at - 3.12) / 1.12);
}

export function teaPoint(x: number, y: number, amount: number, breath: number): [number, number] {
  // Uniform perspective scale keeps the tray, saucers and both grips registered.
  return [x + (x - 600) * .067 * amount, y + ((y - 615) * .067 + 16) * amount - .52 * breath];
}

export function cleanPoint(x: number, y: number, sweep: number, breath: number): [number, number] {
  const angle = (sweep < 0 ? 12.5 : 7.5) * sweep * Math.PI / 180;
  const dx = x - grip[0], dy = y - grip[1];
  const c = Math.cos(angle), s = Math.sin(angle);
  return [grip[0] + c*dx - s*dy + 4.5*sweep, grip[1] + s*dx + c*dy - .52*breath];
}

export function maidLayer(name: string, x: number, y: number, kind: string, frame: MaidFrame, breath: number): [number, number] | undefined {
  if (name === 'serving') {
    if (kind !== 'prop' && kind !== 'finger' && kind !== 'handL' && kind !== 'handR') return;
    const [tx, ty] = teaPoint(x, y, frame.offer, breath);
    const weight = kind.startsWith('hand') ? 1 - ease((y - 644) / 87) : 1;
    return [x + (tx-x)*weight, y + (ty-y)*weight];
  }
  if (name !== 'cleaning') return;
  if (kind.startsWith('dust-')) {
    const particle = frame.dust[Number(kind.slice(5))];
    if (particle) return [particle.x + x*particle.size, particle.y + y*particle.size];
    return [x,y];
  }
  if (kind === 'cleanGrip') {
    const [tx, ty] = cleanPoint(x, y, frame.sweep, breath);
    // Keep the feather, ferrule and fingers rigid. Ease the lower arm into the fixed sleeve.
    const wrist = ease((y - 523) / 50);
    const sleeve = ease((x - 344) / 35);
    const weight = (1 - ease((y - 510) / 143)) * (1 - wrist * sleeve);
    return [x + (tx-x)*weight, y + (ty-y)*weight];
  }
}
