import { ease, GestureConfig } from './street_pose_gestures';

type Point = [number, number];
type Event = { elapsed: number; start: number; hold: number; count: number };
export type PhoneSmile = { schedule: GestureConfig; mouthForm: number; eyeNarrowing: number };
export type SailorRig = {
  schedule: GestureConfig;
  thumb?: { root: Point; tip: Point; edge: number; tapTravel: number; scrollTravel: number };
  smile?: PhoneSmile;
  camera?: { leftWrist: Point; rightWrist: Point; lens: Point; fingerRoot: Point;
    button: Point; raise: Point; rise: number; pause: number; lower: number; flash: number; gripRevision?: boolean };
};
export type SailorFrame = {
  mode: 'idle' | 'tap' | 'scroll' | 'raise' | 'aim' | 'shutter' | 'lower';
  start: number | null;
  elapsed: number;
  taps: number;
  contact: number;
  thumb: Point;
  elevation: number;
  press: number;
  flash: number;
  glow: number;
  gazeX: number;
  gazeY: number;
  pitch: number;
};

const envelope = (time: number, attack: number, hold: number, release: number) =>
  ease(time / attack) * (1 - ease((time - attack - hold) / release));

export function phoneSmileAmount(config: PhoneSmile, event: Event | null): number {
  if (!event) return 0;
  return envelope(event.elapsed,config.schedule.close!,event.hold,config.schedule.open!);
}

export function sailorFrame(rig: SailorRig, event: Event | null): SailorFrame {
  const frame: SailorFrame = {mode:'idle', start:event?.start ?? null, elapsed:event?.elapsed ?? 0,
    taps:0, contact:0, thumb:[0,0], elevation:0, press:0, flash:0, glow:0, gazeX:0, gazeY:0, pitch:0};
  if (!event) return frame;
  const t = event.elapsed;
  if (rig.thumb) {
    const {tapTravel,scrollTravel} = rig.thumb;
    if (event.count === 0) {
      const pressure = envelope(t-.15,.35,.59,.27);
      const travel = ease((t-.52)/.54) * (1-ease((t-1.24)/.34));
      frame.mode = 'scroll';
      frame.contact = pressure;
      frame.thumb = [tapTravel*pressure,-scrollTravel*travel*(event.hold > .5 ? 1 : -1)];
      frame.gazeY = -.055*travel;
    } else {
      frame.mode = 'tap';
      frame.taps = event.count;
      for (let i=0; i<event.count; i++) {
        const onset = .22 + i * (.43 + event.hold*.09) + i*i*.024;
        const strike = envelope(t-onset,.12+i*.014,.055+(i%2)*.013,.13);
        const lift = envelope(t-onset+.08,.039,.025,.05);
        frame.contact = Math.max(frame.contact,strike);
        frame.thumb[0] += tapTravel*strike - 2.3*lift;
        frame.thumb[1] += (i%2 ? -.9 : .65)*strike-2.2*lift;
      }
    }
    return frame;
  }
  const {rise,pause,lower,flash} = rig.camera!;
  const release = rise + event.hold + pause;
  const exposure = rise + event.hold;
  const after = t-exposure;
  frame.elevation = ease(t/rise)*(1-ease((t-release)/lower));
  frame.press = envelope(after+.14,.14,.05,.15);
  if (after >= 0) {
    frame.flash = (1-ease(after/flash))**1.25;
    frame.glow = .67*(1-ease(after/(flash+.095)));
  }
  frame.mode = t < rise ? 'raise' : t >= release ? 'lower' : Math.abs(after+.045)<.16 ? 'shutter' : 'aim';
  frame.pitch = 2.4*frame.elevation;
  frame.gazeX = -.08*frame.elevation;
  frame.gazeY = .14*frame.elevation;
  return frame;
}

export function sailorPoint(x:number,y:number,kind:string,breath:number,frame:SailorFrame,rig:SailorRig):Point | undefined {
  if (rig.thumb) {
    if (kind === 'phoneThumb') {
      const {root,tip,edge} = rig.thumb;
      const blend = ease((x-root[0])/(tip[0]-root[0]));
      const nx = x + frame.thumb[0]*blend;
      // The screen is behind the opaque phone back in this view.
      return [x < edge ? nx : x+Math.min(0,frame.thumb[0])*blend,y-.52*breath+frame.thumb[1]*blend];
    }
    if (kind.startsWith('phone')) return [x,y-.52*breath];
    return undefined;
  }
  const camera = rig.camera!;
  const dx = frame.elevation*camera.raise[0];
  const dy = frame.elevation*camera.raise[1];
  if (kind === 'cameraSleeveL' || kind === 'cameraSleeveR' || kind === 'cameraCuffL' || kind === 'cameraCuffR') {
    const wrist=kind.endsWith('L') ? camera.leftWrist : camera.rightWrist;
    const weight=1-ease((y-wrist[1]-27)/(653-wrist[1]-27));
    return [x+dx*weight,y+dy*weight+breath*(-1.1+.58*weight)];
  }
  if (!kind.startsWith('camera')) return undefined;
  if(camera.gripRevision && kind === 'cameraRightHand') {
    // The palm and the index base share vertices; only the distal phalanges follow the press.
    const knuckle=ease((x-490)/(camera.button[0]-490));
    const index=1-ease((y-400)/20);
    return [x+dx-.38*frame.press*knuckle*index,y+dy-.52*breath+6.5*frame.press*knuckle*index];
  }
  const shape = kind === 'cameraIndex' ? ease((x-camera.fingerRoot[0]) / (camera.button[0]-camera.fingerRoot[0])) : 0;
  const press = kind === 'cameraIndex' ? 5.7*shape : kind === 'cameraShutter' ? 1.65 : 0;
  return [x+dx,y+dy-.52*breath+frame.press*press];
}

export function sailorOpacity(kind:string,frame:SailorFrame) {
  if (kind === 'cameraFlash') return frame.flash;
  if (kind === 'cameraGlow') return frame.glow;
  if (kind === 'phoneFeedback') return .26*frame.contact;
  return 1;
}
