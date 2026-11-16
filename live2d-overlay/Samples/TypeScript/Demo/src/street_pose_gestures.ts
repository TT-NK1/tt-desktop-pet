export type Range = [number, number];
export type GestureConfig = {
  interval: Range;
  hold?: Range;
  duration?: number;
  close?: number;
  open?: number;
  count?: Range;
  secondsPerCount?: number;
};

export const ease = (x: number) => {
  const p = Math.max(0, Math.min(1, x));
  return p * p * (3 - 2 * p);
};
const sample = ([low, high]: Range) => low + (high - low) * Math.random();

export class PoseGesture {
  private next = Infinity;
  private once = false;
  private active: {start: number; duration: number; hold: number; count: number} | null = null;

  constructor(private readonly config?: GestureConfig) {}

  reset(firstDelay?: number): void {
    this.active = null;
    this.once = firstDelay !== undefined;
    this.next = this.config ? firstDelay ?? sample(this.config.interval) : Infinity;
  }

  advance(time: number, blocked = false) {
    if (!this.config) return null;
    if (this.active && time >= this.active.start + this.active.duration) {
      this.next = this.once ? Infinity : this.active.start + this.active.duration + sample(this.config.interval);
      this.active = null;
    }
    if (!this.active && time >= this.next && !blocked) {
      const hold = this.config.hold ? sample(this.config.hold) : 0;
      const count = this.config.count ? Math.floor(sample([this.config.count[0],this.config.count[1]+1])) : 0;
      this.active = {start: time, hold, count,
        duration: this.config.duration ?? this.config.close! + hold + this.config.open! +
          count*(this.config.secondsPerCount??0)};
    }
    return this.active && {...this.active, elapsed: time - this.active.start};
  }
}

export type PageTurn = {
  elapsed: number;
  phase: number;
  reach: number;
  pinch: number;
  shadow: number;
  gaze: number;
};

export function pageTurn(elapsed: number, duration: number): PageTurn {
  const t = elapsed / duration;
  return {
    elapsed,
    phase: ease((t - .35) / .46),
    reach: ease((t - .11) / .24) * (1 - ease((t - .43) / .27)),
    pinch: ease((t - .24) / .11) * (1 - ease((t - .40) / .12)),
    shadow: ease((t - .26) / .14) * (1 - ease((t - .76) / .18)),
    gaze: ease((t - .11) / .24) * (1 - ease((t - .81) / .19)),
  };
}

/** Project a curved leaf around its spine. u goes from spine to free edge. */
export function pagePoint(u: number, v: number, phase: number): [number, number] {
  const angle = phase * Math.PI;
  const bend = .72 * Math.sin(angle) * Math.sin(u * Math.PI);
  const yaw = angle - bend;
  const width = 180 - 18 * v;
  const spine = 628 + 7 * v;
  return [595 + width * u * Math.cos(yaw),
    spine - u * (59 - 26 * v) - u * (59 + 11 * v) * Math.sin(yaw)];
}
