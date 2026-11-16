import { CubismModel } from '@framework/model/cubismmodel';

const frontHair = new Set([
  'Hair_Bang_ViewerLeft', 'Hair_Bang_Center', 'Hair_Bang_ViewerRight',
  'Hair_Lock_ViewerLeft', 'Hair_Lock_ViewerRight',
]);
const vertexPositionsDidChange = 1 << 5;

function smoothstep(value: number): number {
  const t = Math.min(1, Math.max(0, value));
  return t * t * (3 - 2 * t);
}

// The head, neck, clothing and masks use the same model-space displacement.
// Sway tapers toward the feet. The secondary hair motion tapers toward its roots.
export class StreetIdle {
  constructor(private readonly model: CubismModel) {
    const core = model.getModel();
    const d = core.drawables;
    this.hair = d.ids.map(id => frontHair.has(id));
    this.original = Array.from(d.vertexCounts, count => new Float32Array(count * 2));
    this.flags = new Uint8Array(d.count);
    const ids = core.parameters.ids;
    this.breath = ids.indexOf('ParamBreath');
    this.sway = ids.indexOf('ParamBodyAngleZ');
    this.fringe = ids.indexOf('ParamHairFront');
  }

  draw(drawModel: () => void): void {
    const core = this.model.getModel();
    const { values } = core.parameters;
    const d = core.drawables;
    const breath = (values[this.breath] || 0) - 0.5;
    const sway = values[this.sway] || 0;
    const fringe = values[this.fringe] || 0;
    this.flags.set(d.dynamicFlags);
    for (let i = 0; i < d.count; i++) {
      const vertices = d.vertexPositions[i];
      this.original[i].set(vertices);
      d.dynamicFlags[i] |= vertexPositionsDidChange;
      for (let j = 0; j < vertices.length; j += 2) {
        const y = vertices[j + 1];
        const swayWeight = smoothstep((y + 0.76) / 1.70);
        const breathWeight = smoothstep((y + 0.70) / 0.95);
        const hairWeight = this.hair[i] ? smoothstep((0.81 - y) / 0.43) : 0;
        vertices[j] += 0.00225 * sway * swayWeight + 0.002 * breath * breathWeight
          + 0.008 * fringe * hairWeight;
        vertices[j + 1] += 0.012 * breath * breathWeight + 0.0025 * fringe * hairWeight;
      }
    }
    try {
      drawModel();
    } finally {
      for (let i = 0; i < d.count; i++) {
        d.vertexPositions[i].set(this.original[i]);
      }
      d.dynamicFlags.set(this.flags);
    }
  }

  private readonly hair: boolean[];
  private readonly original: Float32Array[];
  private readonly flags: Uint8Array;
  private readonly breath: number;
  private readonly sway: number;
  private readonly fringe: number;
}
