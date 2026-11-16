import { CubismModel } from '@framework/model/cubismmodel';
import { CubismRenderer_WebGL } from '@framework/rendering/cubismrenderer_webgl';
import { FacePixels, FacePose, faceRect, smooth } from './face_pixels';

const aperture = new Float32Array([0, 1, 0, 0, 1, 1, 1, 0]);
const triangles = new Uint16Array([0, 1, 2, 2, 1, 3]);
const gazeStops = [[0, 0], [.10, .06], [-.13, .02], [.08, -.09], [-.03, .11], [0, 0]];
const rect = faceRect;

type FaceGeometry = { canvas: number[]; anchors: number[]; rest: number[] };
const parameterIds = ['ParamEyeBallX', 'ParamEyeBallY', 'ParamEyeLOpen', 'ParamEyeROpen', 'ParamMouthOpenY'];

export class FaceRig {
  private readonly raw: Live2DCubismCore.Model;
  private readonly slot: number;
  private readonly base: number;
  private readonly hair: number;
  private readonly channels: number[];
  private readonly quad = new Float32Array(8);
  private readonly gl: WebGLRenderingContext;
  private readonly texture: WebGLTexture;
  private readonly atlasSlot: number;
  private readonly coords: number[][];
  private lastPose: FacePose | null = null;

  static async load(
    folder: string, core: CubismModel, renderer: CubismRenderer_WebGL,
  ): Promise<FaceRig> {
    const [open, closed, skin, geometry] = await Promise.all([
      this.loadPlate(folder + 'face-open.png'),
      this.loadPlate(folder + 'face-closed.png'),
      this.loadPlate(folder + 'face-skin.png'),
      fetch(folder + 'face-geometry.json').then(async response => {
        if (!response.ok) throw new Error('Face geometry could not be loaded');
        return await response.json() as FaceGeometry;
      }),
    ]);
    return new FaceRig(core, renderer, new FacePixels(open, closed, skin), geometry);
  }

  private static async loadPlate(url: string): Promise<Uint8ClampedArray> {
    const img = new Image();
    img.src = url;
    await img.decode();
    if (img.width !== rect.width || img.height !== rect.height) throw new Error('Invalid face plate dimensions');
    const canvas = document.createElement('canvas');
    canvas.width = rect.width;
    canvas.height = rect.height;
    const context = canvas.getContext('2d')!;
    context.drawImage(img, 0, 0);
    return context.getImageData(0, 0, rect.width, rect.height).data;
  }

  private constructor(
    core: CubismModel,
    renderer: CubismRenderer_WebGL,
    private readonly pixels: FacePixels,
    private readonly geometry: FaceGeometry,
  ) {
    this.raw = core.getModel();
    const drawables = this.raw.drawables;
    this.slot = drawables.ids.indexOf('mouth_cavity');
    this.base = drawables.ids.indexOf('face_feature_underpaint');
    this.hair = drawables.ids.indexOf('hair_back');
    this.channels = parameterIds.map(id => this.raw.parameters.ids.indexOf(id));
    if ([this.slot, this.base, this.hair, ...this.channels].includes(-1)) throw new Error('Model lacks face rig inputs');
    const [,, ox, oy, ppu] = geometry.canvas;
    const [ax, ay, bx, by, cx, cy] = geometry.rest;
    const determinant = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay);
    if (Math.abs(determinant) < 1e-6) throw new Error('Invalid face anchors');
    this.coords = [[0, 0], [0, rect.height], [rect.width, 0], [rect.width, rect.height]].map(([x, y]) => {
      const dx = (rect.x + x - ox) / ppu - ax, dy = (oy - rect.y - y) / ppu - ay;
      return [(dx * (cy - ay) - dy * (cx - ax)) / determinant, (dy * (bx - ax) - dx * (by - ay)) / determinant];
    });
    this.gl = renderer.gl;
    const texture = this.gl.createTexture();
    if (!texture) throw new Error('Could not allocate face texture');
    this.texture = texture;
    this.atlasSlot = Math.max(...drawables.textureIndices) + 1;
    this.withTexture(() => {
      const gl = this.gl;
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, rect.width, rect.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    });
  }

  get pose(): FacePose | null {
    return this.lastPose;
  }

  dispose(): void {
    this.gl.deleteTexture(this.texture);
  }

  private withTexture(action: () => void): void {
    const gl = this.gl;
    const binding = gl.getParameter(gl.TEXTURE_BINDING_2D);
    const premultiply = gl.getParameter(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    try {
      action();
    } finally {
      gl.bindTexture(gl.TEXTURE_2D, binding);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, premultiply);
    }
  }

  draw(renderer: CubismRenderer_WebGL, seconds: number): void {
    let [gazeX, gazeY, leftOpen, rightOpen, mouth] = this.channels.map(i => this.raw.parameters.values[i]);
    const phase = seconds / 1.45;
    const stop = Math.floor(phase) % gazeStops.length;
    const next = gazeStops[(stop + 1) % gazeStops.length];
    const blend = smooth(0, .18, phase - Math.floor(phase));
    const strength = 1 - smooth(.05, .46, Math.hypot(gazeX, gazeY));
    gazeX += strength * (gazeStops[stop][0] * (1 - blend) + next[0] * blend);
    gazeY += strength * (gazeStops[stop][1] * (1 - blend) + next[1] * blend);
    const cap = Math.max(1, Math.hypot(gazeX, gazeY));
    const pose = { gazeX: gazeX / cap, gazeY: gazeY / cap, leftOpen, rightOpen, mouth };
    const previous = this.lastPose;
    if (!previous || (Object.keys(pose) as Array<keyof FacePose>).some(key => Math.abs(pose[key] - previous[key]) > .003)) {
      this.withTexture(() => {
        const gl = this.gl;
        gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, rect.width, rect.height, gl.RGBA, gl.UNSIGNED_BYTE, this.pixels.render(pose));
      });
      this.lastPose = pose;
    }

    // Carry the continuous plate with the existing breathing and body deformer.
    const d = this.raw.drawables;
    const anchors = d.vertexPositions[this.base];
    const [a, b, c] = this.geometry.anchors;
    this.coords.forEach(([u, v], i) => {
      for (let axis = 0; axis < 2; axis++) {
        this.quad[i * 2 + axis] = anchors[a + axis]
          + u * (anchors[b + axis] - anchors[a + axis])
          + v * (anchors[c + axis] - anchors[a + axis]);
      }
    });

    // Borrow the mouth drawable for the face plate during this draw only. Place it
    // above the face and hair while preserving foreground sleeves and body layers.
    const i = this.slot;
    const order = d.renderOrders.slice();
    const front = order[this.hair], old = order[i];
    const vertices = d.vertexPositions[i], uvs = d.vertexUvs[i], indices = d.indices[i];
    const vertexCount = d.vertexCounts[i], indexCount = d.indexCounts[i];
    const texture = d.textureIndices[i], opacity = d.opacities[i], flags = d.dynamicFlags[i];
    for (let j = 0; j < d.count; j++) if (order[j] > old && order[j] <= front) d.renderOrders[j]--;
    d.renderOrders[i] = front;
    d.vertexPositions[i] = this.quad;
    d.vertexUvs[i] = aperture;
    d.indices[i] = triangles;
    d.vertexCounts[i] = 4;
    d.indexCounts[i] = 6;
    d.textureIndices[i] = this.atlasSlot;
    d.opacities[i] = 1;
    d.dynamicFlags[i] |= 1;
    renderer.bindTexture(this.atlasSlot, this.texture);
    try {
      renderer.drawModel();
    } finally {
      d.renderOrders.set(order);
      d.vertexPositions[i] = vertices;
      d.vertexUvs[i] = uvs;
      d.indices[i] = indices;
      d.vertexCounts[i] = vertexCount;
      d.indexCounts[i] = indexCount;
      d.textureIndices[i] = texture;
      d.opacities[i] = opacity;
      d.dynamicFlags[i] = flags;
    }
  }
}

