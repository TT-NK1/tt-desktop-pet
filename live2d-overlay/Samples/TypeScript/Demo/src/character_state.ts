// Same-origin structured state transfer, never framebuffer or desktop capture.
export const portraitMode = new URLSearchParams(location.search).has('portrait');
export type CharacterFrame = {
  sequence:number; sentAt:number; model:string; time:number; poseName:string|null;
  pose:any; opacity:number; parameters:number[]; parts:number[];
};
export let incomingFrame:CharacterFrame|null = null;
export const mirrorStats = { publishing:false, published:0, received:0, applied:0, latencyMs:0 };
export const portraitExpression = { name: null as string|null, previous: null as string|null, changedAt: 0 };
let channel:BroadcastChannel|null = null;

export function setPortraitExpression(name:string|null):boolean {
  if (!portraitMode || (name !== null && !/^(?:E0[1-9]|Sleepy)$/.test(name))) return false;
  if (name !== portraitExpression.name) {
    portraitExpression.previous = portraitExpression.name;
    portraitExpression.name = name;
    portraitExpression.changedAt = performance.now();
  }
  return true;
}

export function setCharacterPublisher(name:string|null):boolean {
  if (portraitMode) return false;
  channel?.close();
  channel = name ? new BroadcastChannel(name) : null;
  mirrorStats.publishing = !!channel;
  return true;
}

export function setCharacterSubscriber(name:string):boolean {
  if (!portraitMode) return false;
  channel?.close();
  incomingFrame=null;
  mirrorStats.received=0; mirrorStats.applied=0;
  document.documentElement.style.opacity='0';
  if (name) {
    channel = new BroadcastChannel(name);
    channel.onmessage = event => {
      const frame=event.data as CharacterFrame;
      if (!frame || !Number.isFinite(frame.sequence) || frame.sequence <= (incomingFrame?.sequence ?? 0)) return;
      incomingFrame = frame;
      mirrorStats.received++;
    };
  }
  return true;
}
if (portraitMode) setCharacterSubscriber(new URLSearchParams(location.search).get('stateChannel'));

export function publishCharacterFrame(model:any, sequence:number):void {
  if (!channel || portraitMode) return;
  const core = model.getModel().getModel();
  const frame:CharacterFrame = {
    sequence, sentAt:Date.now(), model:model._modelHomeDir, time:model._userTimeSeconds,
    poseName:model.streetPoseName, pose:model.streetPose, opacity:model.streetPoseOpacity,
    parameters:Array.from(core.parameters.values), parts:Array.from(core.parts.opacities),
  };
  channel.postMessage(frame);
  mirrorStats.published = sequence;
}

export function closeCharacterChannel():void {
  channel?.close(); channel=null; mirrorStats.publishing=false;
}
