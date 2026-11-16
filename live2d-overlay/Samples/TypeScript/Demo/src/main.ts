import { transitionTime } from "./desktop_switch";
import { poseFolders } from "./street_poses";
// Desktop Pet validation adapter; official rendering stays in the bundled SDK.
import { portraitMode, incomingFrame, setCharacterPublisher, setCharacterSubscriber, publishCharacterFrame, mirrorStats, closeCharacterChannel, portraitExpression, setPortraitExpression } from './character_state';
import { LAppDelegate } from './lappdelegate';
import { LAppPal } from './lapppal';
import { CubismFramework } from '@framework/live2dcubismframework';

let app: any = null;
let frames = 0, fps = 60, last = 0, deadline = 0, first = 0, raf = 0, disposed = false;
const errors: string[] = [];
const frameTimes: number[] = [];
let finished = 0, began = 0;
let outfitMotionFinished = true;
const defaultOutfit = new URLSearchParams(location.search).get('outfit');
let defaultOutfitRequested = false;
let defaultOutfitReady = !defaultOutfit;
let baseOutfit = defaultOutfit;
let stretchPhase: 'playing' | 'returning' | null = null;
let pendingLook = false;
let outfitRequest = 0;
let actionRequest = 0;
let requestedOutfit: {id: string | null; token: number; poseReady: boolean} | null = null;
let completedOutfit = 0;
let actionId: string | null = null;
let autoAction: string | null = null;
let autoReturning = false;
const autoFadeIn = .250, autoFadeOut = .300;

function clearAutoAction(): void {
  autoAction=null;
  autoReturning=false;
}
let queuedAction: (() => void) | null = null;
let switchError: {token: number; message: string} | null = null;
let actionError: {id: string; message: string} | null = null;

function keyMoment(model: any): boolean {
  const pose=model?.streetPose;
  const offer=pose?.maid?.elapsed;
  return (model?.streetPoseName==='serving' && offer!=null && offer>=1.72 && offer<1.90)
    || (model?.streetPoseName==='photo' && pose?.sailor?.mode==='shutter');
}

function basePoseReady(model: any) {
  return (model?.streetPoseName ?? null) === baseOutfit && model?.streetPoseOpacity === 1;
}
let lastHash = 0;
const hashes = new Set<number>();
function parts() {
  const sub = app?._subdelegates?.at(0);
  return {sub, manager: sub?.getLive2DManager(), model: sub?.getLive2DManager()?._models?.at(0), gl: sub?.getGlManager()?.getGl()};
}
function loop(now: number) {
  if (disposed) return;
  raf = requestAnimationFrame(loop);
  renderFrame(now);
}
function renderFrame(now: number) {
  if (disposed) return;
  // A paused tool window still needs one painted frame to acknowledge a requested outfit.
  const forceOutfitFrame=!fps && requestedOutfit?.poseReady && (!document.hidden || mirrorStats.publishing);
  if ((!fps && !forceOutfitFrame) || (document.hidden && !mirrorStats.publishing)) { LAppPal.updateTime(); last = 0; deadline = 0; return; }
  if (deadline && !forceOutfitFrame && now < deadline - 1) return;
  const interval = 1000 / (fps || 24);
  deadline = deadline && now-deadline<3*interval ? deadline+interval : now+interval;
  if (last) {frameTimes.push(now-last); if(frameTimes.length>600) frameTimes.shift();}
  last = now;
  LAppPal.updateTime();
  if (portraitMode && parts().model?._state === 22 && incomingFrame?.sequence === mirrorStats.applied && !parts().sub?._needResize) return;
  for (let i=0;i<app._subdelegates.getSize();i++) app._subdelegates.at(i).update();
  const {model,gl} = parts();
  if (model?._state === 22) {
    if (!portraitMode && defaultOutfit && !defaultOutfitRequested && model.poseReadiness.includes(defaultOutfit)) {
      defaultOutfitRequested = api.showOutfit(defaultOutfit);
    }
    if (portraitMode) defaultOutfitReady = mirrorStats.applied > 0;
    if (!defaultOutfitReady && basePoseReady(model) && !model.streetPoseChanging) defaultOutfitReady = true;
    if (autoAction && model.streetPoseName===autoAction && model.autonomousPoseDone) {
      model.setStreetPose(baseOutfit,autoFadeOut);
      clearAutoAction();
      autoReturning=true;
    }
    if (autoReturning && basePoseReady(model) && !model.streetPoseChanging) {
      clearAutoAction();
      actionId=null;
      finished++;
    }
    if (stretchPhase === 'playing' && model.streetPoseName === 'stretch' && model.streetPose?.time >= 5.3) {
      model.setStreetPose(baseOutfit,autoAction ? autoFadeOut : transitionTime.action);
      stretchPhase = 'returning';
    }
    if (stretchPhase === 'returning' && basePoseReady(model) && !model.streetPoseChanging) {
      stretchPhase = null;
      actionId=null;
      clearAutoAction();
      finished++;
    }
    if (pendingLook && basePoseReady(model) && !model.streetPoseChanging) {
      pendingLook = false;
      api.lookOutfit();
    }
    if (requestedOutfit?.poseReady && model.streetPoseName === requestedOutfit.id) {
      completedOutfit = requestedOutfit.token;
      requestedOutfit = null;
    }
    if (queuedAction && !keyMoment(model)) {
      const queued=queuedAction;
      queuedAction=null;
      queued();
    }
    if (!first) first = now;
    frames++;
    if (!portraitMode) publishCharacterFrame(model, frames);
    // Read the rendered framebuffer during the render task, before it is discarded.
    if (frames % 30 === 0 && (!new URLSearchParams(location.search).has('desktop') || new URLSearchParams(location.search).has('verify'))) {
      const data = new Uint8Array(gl.drawingBufferWidth*gl.drawingBufferHeight*4);
      gl.readPixels(0,0,gl.drawingBufferWidth,gl.drawingBufferHeight,gl.RGBA,gl.UNSIGNED_BYTE,data);
      let hash=2166136261;
      for(let j=0;j<data.length;j+=16) hash=Math.imul(hash ^ data[j],16777619);
      lastHash=hash>>>0;hashes.add(lastHash);
    }
  }
}

let bindingAudit:any = null;
function auditBindings(core:any) {
  const saved = Array.from({length:core.getParameterCount()},(_,i)=>core.getParameterValueByIndex(i));
  const state = () => {
    const values:number[]=[];
    for(let d=0;d<core.getDrawableCount();d++) {
      values.push(core.getDrawableOpacity(d));
      for(const v of core.getDrawableVertices(d)) values.push(v);
    }
    return values;
  };
  const result:any[]=[];
  try {
    for(let i=0;i<saved.length;i++) {
      saved.forEach((v,j)=>core.setParameterValueByIndex(j,v));
      core.setParameterValueByIndex(i,core.getParameterMinimumValue(i));core.update();
      const low=state();
      core.setParameterValueByIndex(i,core.getParameterMaximumValue(i));core.update();
      const high=state();
      let changed=0,maxDelta=0;
      low.forEach((v,j)=>{const delta=Math.abs(v-high[j]);if(delta>1e-6)changed++;maxDelta=Math.max(maxDelta,delta);});
      result.push({id:core.getParameterId(i).getString().s,changedValues:changed,maxDelta,bound:changed>0});
    }
  } finally {saved.forEach((v,j)=>core.setParameterValueByIndex(j,v));core.update();}
  return result;
}

function snapshot() {
  const {model,gl} = parts();
  const core = model?.getModel();
  if(core && !bindingAudit && model?._state===22) bindingAudit=auditBindings(core);
  const ranges: Record<string,number[]> = {};
  if(core) for(let i=0;i<core.getParameterCount();i++) ranges[core.getParameterId(i).getString().s]=[core.getParameterMinimumValue(i),core.getParameterMaximumValue(i),core.getParameterDefaultValue(i),core.getParameterValueByIndex(i)];
  const ext=gl?.getExtension('WEBGL_debug_renderer_info');
  const sorted=[...frameTimes].sort((a,b)=>a-b);
  const faceRequired = model?._modelHomeDir?.endsWith('/street_knees/');
  const faceReady = !!model?.faceRig?.pose;
  const streetIdleParameters = ['ParamBreath', 'ParamBodyAngleZ', 'ParamHairFront'];
  const audit = model?.usesStreetIdle() ? bindingAudit?.map((row: any) =>
    streetIdleParameters.includes(row.id) ? {...row, bound: true, driver: 'streetIdle'} : row)
    : faceReady ? bindingAudit?.map((row: any) =>
    row.id === 'ParamMouthOpenY' ? {...row, bound: true, driver: 'facePlate'} : row) : bindingAudit;
  const outfitError = defaultOutfit ? model?.streetPoseErrors?.[defaultOutfit] : undefined;
  return {ready:model?._state===22 && (!faceRequired || faceReady)
    && defaultOutfitReady,model:model?._modelHomeDir,frames,
    facePose:model?.faceRig?.pose,
    streetPose:model?.streetPose,streetPoseName:model?.streetPoseName,poseReadiness:model?.poseReadiness,streetPoseErrors:model?.streetPoseErrors,
    streetPoseOpacity:model?.streetPoseOpacity,outfitMotionFinished,
    measuredFps:frameTimes.length?1000/(frameTimes.reduce((a,b)=>a+b,0)/frameTimes.length):0,
    p95FrameMs:sorted[Math.floor(sorted.length*.95)]||0,targetFps:fps,
    renderSeconds:first?(performance.now()-first)/1000:0,
    glRenderer:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl?.getParameter(gl.RENDERER),
    contextLost:gl?.isContextLost(),glError:gl?.getError(),drawableCount:core?.getDrawableCount(),
    textures:model?._textureCount,parameters:ranges,frameHash:lastHash,distinctFrames:hashes.size,
    switching:model?.streetPoseChanging, completedOutfit, switchError, actionError,
    performance:{modelMs:model?.modelMs,textureMs:model?.textureMs,cache:model?.desktopCacheStats},
    bindingAudit:audit,motionBegan:began,motionFinished:finished,errors:[...errors,...[model?.faceError,outfitError].filter(Boolean)],disposed};
}
const api = {
  snapshot,
  setCharacterPublisher,
  setCharacterSubscriber,
  setPortraitExpression,
  advanceHidden() { if (!portraitMode && mirrorStats.publishing) renderFrame(performance.now()); },
  characterState() {
    const m=parts().model;
    const core=m?.getModel()?.getModel();
    return { ...mirrorStats, frames, ready:m?._state===22, pose:m?.streetPoseName, expression:portraitExpression.name,
      poseTime:m?.streetPose?.time, parameters:core?Array.from(core.parameters.values):[],
      parts:core?Array.from(core.parts.opacities):[], poseState:m?.streetPose,
      modelIdentity:m?._modelHomeDir, loadedPoses:m?.poseReadiness, heapBytes:(performance as any).memory?.usedJSHeapSize,
      error:errors, contextLost:parts().gl?.isContextLost(), targetFps:fps };
  },
  parameters(values:Record<string,number>) {
    const core=parts().model?.getModel(); if(!core) return false;
    const safe:Record<string,number>={};
    for(let i=0;i<core.getParameterCount();i++) {
      const id=core.getParameterId(i).getString().s, value=values[id];
      if(Number.isFinite(value)) safe[id]=Math.max(core.getParameterMinimumValue(i),Math.min(core.getParameterMaximumValue(i),value));
    }
    (window as any).petParameters={values:safe,until:performance.now()+250}; return true;
  },
  selectOutfit(id: string | null, token: number) {
    const m=parts().model;
    if (!m?.fastDesktopSwitch || m._state!==22) return false;
    if (id && !poseFolders[id]) return false;
    outfitRequest=token;
    const oldRequest=requestedOutfit;
    if (oldRequest && !oldRequest.poseReady) m.dropWaitingDesktopPoses();
    switchError=null;
    requestedOutfit={id,token,poseReady:false};
    if (id===baseOutfit && !m.streetPoseChanging) {
      completedOutfit=token;
      requestedOutfit=null;
      return true;
    }
    const ready = () => {
      if (outfitRequest!==token || disposed) return;
      ++actionRequest;
      clearAutoAction();
      stretchPhase=null;
      pendingLook=false;
      actionId=null;
      actionError=null;
      queuedAction=null;
      m.idleOutfit();
      m.setStreetPose(id,fps ? transitionTime.outfit : 0);
      baseOutfit=id;
      m.warmDesktopOutfit(id);
      if (requestedOutfit?.token===token) requestedOutfit.poseReady=true;
      outfitMotionFinished=true;
    };
    if (!id || m.poseReadiness.includes(id)) ready();
    else m.prepareDesktopPose(id).then(ready, (error: unknown)=>{
      if (outfitRequest===token) {
        requestedOutfit=null;
        switchError={token,message:String(error)};
      }
    });
    return true;
  },
  cancelOutfit(token: number) {
    if (outfitRequest===token && requestedOutfit && !requestedOutfit.poseReady) {
      ++outfitRequest;
      requestedOutfit=null;
    }
    return true;
  },
  showOutfit(id:string) {
    const m=parts().model;
    if(!m?.poseReadiness.includes(id))return false;
    m.idleOutfit();
    m.setExpression('E01');
    baseOutfit=id;
    stretchPhase=null;
    pendingLook=false;
    outfitMotionFinished=true;
    return m.setStreetPose(id,transitionTime.outfit);
  },
  idleOutfit() {
    const m=parts().model;
    if(!m)return false;
    m.idleOutfit();
    outfitMotionFinished=true;
    return true;
  },
  lookOutfit() {
    const m=parts().model;
    if(!m || !m._modelSetting.getMotionCount('OutfitLookAround'))return false;
    m.idleOutfit();
    stretchPhase=null;
    if (!basePoseReady(m) || m.streetPoseChanging) {
      pendingLook=true;
      outfitMotionFinished=false;
      return m.setStreetPose(baseOutfit);
    }
    outfitMotionFinished=false;
    m.startMotion('OutfitLookAround',0,3,()=>{finished++;outfitMotionFinished=true;},()=>began++);
    return true;
  },
  clearStreetPose() {
    const m=parts().model;
    if (m?.fastDesktopSwitch) {
      ++actionRequest;
      clearAutoAction();
      if (!requestedOutfit) m.dropWaitingDesktopPoses();
      actionId=null;
      actionError=null;
      const settle = () => {
        stretchPhase=null;
        pendingLook=false;
        m.stopDesktopMotion();
        m.beginDesktopBlend(transitionTime.action);
        m.setStreetPose(baseOutfit);
      };
      queuedAction=null;
      if (keyMoment(m)) queuedAction=settle;
      else settle();
      return true;
    }
    stretchPhase=null;
    pendingLook=false;
    return m?.setStreetPose(baseOutfit) ?? false;
  },
  play(group:string,index=0,autoLead?:number) {
    const m=parts().model;
    if (m?.fastDesktopSwitch) {
      const id=group+'_'+index;
      if (actionId===id) return true;
      const custom=!!poseFolders[group];
      if (!custom && (index<0 || index>=m._modelSetting.getMotionCount(group))) return false;
      const token=++actionRequest;
      if (!requestedOutfit) m.dropWaitingDesktopPoses();
      actionError=null;
      actionId=id;
      queuedAction=null;
      const start = () => {
        if (token!==actionRequest || disposed) return;
        if (keyMoment(m)) { queuedAction=start; return; }
        clearAutoAction();
        autoAction=autoLead !== undefined && custom ? group : null;
        stretchPhase=group==='stretch' ? 'playing' : null;
        pendingLook=false;
        m.stopDesktopMotion();
        const fade = autoLead === undefined ? transitionTime.action : autoFadeIn;
        m.beginDesktopBlend(fade);
        if (custom) {
          m.setStreetPose(group,fade,autoLead);
          began++;
        } else {
          m.setStreetPose(baseOutfit);
          const look=group==='OutfitLookAround' || group==='LookAround';
          const complete = () => {
            if (token!==actionRequest) return;
            actionId=null;
            if (autoLead !== undefined) {
              m.beginDesktopBlend(autoFadeOut);
              autoReturning=true;
            } else finished++;
            if (look) outfitMotionFinished=true;
          };
          if (look) outfitMotionFinished=false;
          m.startMotion(group,index,3,complete,()=>began++,fade);
        }
      };
      if (!custom || m.poseReadiness.includes(group)) start();
      else m.prepareDesktopPose(group).then(start,(error: unknown)=>{
        if (token!==actionRequest) return;
        actionId=null;
        actionError={id,message:String(error)};
      });
      return true;
    }
    if(group==='OutfitLookAround' && index===0)return api.lookOutfit();
    if (m?.poseReadiness.includes(group)) {
      stretchPhase=group==='stretch' ? 'playing' : null;
      pendingLook=false;
      if (!m.setStreetPose(group)) return false;
      began++;
      return true;
    }
    m?.setStreetPose(null);
    if(!m || index<0 || index>=m._modelSetting.getMotionCount(group))return false;
    m.startMotion(group,index,3,()=>finished++,()=>began++);return true;
  },
  namedExpression(name:string) {
    const m=parts().model;
    if(!m?._expressions?.getValue(name)) return false;
    m.setExpression(name); return true;
  },
  sway(value:number|null) {
    (window as any).petSwayOverride = value === null ? null : Math.max(-10,Math.min(10,Number(value)||0));
  },
  setFps(value:number) {fps=Math.max(0,Math.min(60,Number(value)||0)); frameTimes.length=0;last=0;deadline=0;return fps;},
  drag(x:number,y:number) {parts().manager?.onDrag(Math.max(-1,Math.min(1,x)),Math.max(-1,Math.min(1,y)));},
  motion() {const m=parts().model;if(m?._state!==22)return false;m.startMotion('TapBody',0,3,()=>finished++,()=>began++);return true;},
  expression() {const m=parts().model;if(m?._state!==22)return false;m.setRandomExpression();return true;},
  dispose() {
    if(!disposed){
      disposed=true;cancelAnimationFrame(raf);closeCharacterChannel();
      const contexts:any[]=[];
      if(app?._subdelegates) for(let i=0;i<app._subdelegates.getSize();i++) {
        const sub=app._subdelegates.at(i), manager=sub.getLive2DManager();
        contexts.push(sub.getGlManager().getGl());
        for(let j=0;j<manager._models.getSize();j++)manager._models.at(j).release();
        manager._models.clear();
      }
      LAppDelegate.releaseInstance();app=null;
      contexts.forEach(gl=>gl.getExtension('WEBGL_lose_context')?.loseContext());
    }
    return {disposed};
  }
};
(window as any).sdkProbe=api;
window.addEventListener('error',e=>errors.push(e.message));
window.addEventListener('unhandledrejection',e=>errors.push(String(e.reason)));
window.addEventListener('load',()=>{
  const viewport = new URLSearchParams(location.search);
  if (portraitMode || (viewport.has('desktop') && viewport.get('model') === 'street_neck_rebuild_v20')) {
    document.getElementById('outfit-normalization')?.remove();
  }
  if (portraitMode) {
    document.body.style.pointerEvents='none';
    document.documentElement.style.opacity='0';
  }
  app=LAppDelegate.getInstance();
  if(!app.initialize()){errors.push('Cubism initialization failed');return;}
  raf=requestAnimationFrame(loop);
});
window.addEventListener('beforeunload',()=>api.dispose());
