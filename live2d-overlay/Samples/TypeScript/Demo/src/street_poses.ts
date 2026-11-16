import { SailorFrame, SailorRig, phoneSmileAmount, sailorFrame, sailorPoint, sailorOpacity } from './sailor_interactions';
import { MaidFrame, MaidMotion, maidLayer, sweepDuration } from './maid-motion';
import { CubismModel } from '@framework/model/cubismmodel';
import { CubismRenderer_WebGL } from '@framework/rendering/cubismrenderer_webgl';
import { ease as smooth, GestureConfig, pagePoint, PageTurn, pageTurn, PoseGesture } from './street_pose_gestures';
import { craftPoint, craftStitch, Interaction, SipRig, TeaSavor, StitchRig, teaDrink, teaPoint } from './gothic_gestures';

import { bodyComposition, composeBodyVertex } from './character_composition';

type Rect = [number,number,number,number];
type Layer = { name: string; motion: string; file: string; rect: Rect; pivot?: [number,number]; strength?: number };
type Layout = {
  id: string; sharedIdle?: boolean; width: number; height: number;
  anchor: [number,number]; scale: number; roll: number;
  face: [number,number,number,number,number]; mouthForm?: number; layers: Layer[]; irisDownPixels?: number;
  pageTurn?: GestureConfig; eyesClosed?: GestureConfig;
  sailor?: SailorRig; gesture?: GestureConfig; sip?: SipRig; stitch?: StitchRig; bunnyRepair?: BunnyRepair;
};
type Pose = { time: number; breath: number; gazeX: number; gazeY: number; eye: number; lean: number;
  hugSqueeze?: number; readingLine?: number; gripDrift?: number; cupGripDrift?: number; blink?: boolean;
  maid?: MaidFrame; pageTurn?: PageTurn; eyesClosed?: {elapsed: number; hold: number; amount: number}; interaction?:Interaction; savor?:ReturnType<TeaSavor['sample']>; sailor?:SailorFrame; phoneSmile?:number };
export const poseFolders: Record<string,string> = {Reading:'M04',ListeningMusic:'M06',gothic:'gothic',maid:'maid',bunny:'bunny',sailor:'sailor',tea:'tea',handcraft:'handcraft',serving:'serving',cleaning:'cleaning',hug:'hug',stretch:'stretch',phone:'phone',photo:'photo'};
const bodySlots = Array.from({length:14},(_,i)=>'ArtMesh'+(13+i));
const dirty = 1 << 5, visible = 1;
const wave = (t:number, period:number) => Math.sin(t*2*Math.PI/period);
// [end time, horizontal offset, vertical offset]. Stops represent reading a short phrase.
const readingScan = [
  [0,-.09,0], [1.25,-.09,0], [2.2,-.03,0], [2.85,-.03,0], [3.8,.023,0],
  [4.35,.023,0], [4.65,-.083,-.029], [5.45,-.083,-.029], [6.6,.004,-.029],
  [7.1,.004,-.029], [7.65,.064,-.008], [8.25,.064,-.008], [9.65,.164,-.008],
  [10.55,.164,-.008], [10.9,.061,-.04], [11.8,.061,-.04], [12.9,.15,-.04],
  [14.55,.15,-.04], [15.5,-.09,0], [16.2,-.09,0],
] as const;

function readAcrossPage(t:number):[number,number] {
  const at=t%readingScan[readingScan.length-1][0];
  const next=readingScan.findIndex(([end])=>end>at);
  const [start,x,y]=readingScan[next-1], [end,nx,ny]=readingScan[next];
  const amount=smooth((at-start)/(end-start));
  return [x+(nx-x)*amount,y+(ny-y)*amount];
}

export class StreetPose {
  // Register every pose in the native idle model's coordinate system.
  private readonly modelPixels: number;
  private readonly composition: ReturnType<typeof bodyComposition>;
  private readonly slots: number[];
  private readonly crown: number;
  private readonly body: Set<number>;
  private readonly hairTips: Set<number>;
  private readonly irises: Set<number>;
  private readonly sequence: number[];
  private readonly shapes: ReturnType<typeof grid>[];
  private readonly original: Float32Array[];
  private readonly flags: Uint8Array;
  private readonly opacity: Float32Array;
  private readonly orders: Int32Array;
  private readonly ids: Record<string,number>;
  private readonly atlasBase: number;
  public get sharedIdle() { return !!this.layout.sharedIdle; }
  private time=0;
  private readingTime=0;
  private wasTurning=false;
  private autonomous = false;
  private sawGesture = false;
  public autonomousDone = false;
  private readonly gesture: PoseGesture;
  private readonly teaSavor?: TeaSavor;
  private readonly smileGesture?: PoseGesture;
  private readonly maidMotion?: MaidMotion;
  private blinkAt=3.4;
  private blinkCount=0;
  private doubleBlink=false;
  public pose: Pose | null=null;

  private constructor(public readonly name:string, private readonly model:CubismModel,
    private readonly renderer:CubismRenderer_WebGL, private readonly layout:Layout,
    private readonly textures:WebGLTexture[], public readonly bytes:number, public readonly loadMs:number) {
    this.modelPixels = 599.5 * layout.scale;
    this.composition = bodyComposition(name);
    const {drawables:d,parameters:p}=model.getModel();
    this.gesture=name==='hug' ? new PoseGesture({interval:[20,45],close:HUG_RISE,hold:[HUG_HOLD,HUG_HOLD],open:HUG_RELEASE}) : new PoseGesture(layout.pageTurn ?? layout.eyesClosed ?? layout.gesture ?? layout.sailor?.schedule);
    if (name==='serving' || name==='cleaning') this.maidMotion=new MaidMotion(name);
    if(name==='phone' && layout.sailor?.smile) this.smileGesture=new PoseGesture(layout.sailor.smile.schedule);
    if (layout.sip) this.teaSavor=new TeaSavor();
    this.crown=d.ids.indexOf('Hair_Crown');
    // The master uses these two masked drawables for the native eyeball parameters.
    this.irises=new Set(this.layout.irisDownPixels ? ['ArtMesh2','ArtMesh3'].map(id=>d.ids.indexOf(id)) : []);
    this.body=new Set(bodySlots.map(id=>d.ids.indexOf(id)));
    this.hairTips=new Set(d.ids.flatMap((id,i)=>/^Hair_(Lock|Side)/.test(id)?[i]:[]));
    this.slots=bodySlots.slice(0,layout.layers.length).map(id=>d.ids.indexOf(id));
    if(this.body.has(-1)||this.slots.length!==layout.layers.length)throw Error('Street pose needs more body slots');
    this.shapes=layout.layers.map(layer=>grid(layer.rect, layout.bunnyRepair ? (layer.motion==='bunnyBody' ? 60 : 28) :
      layer.motion.startsWith('cameraSleeve')||layer.motion.startsWith('cameraCuff')||layer.motion==='cameraRightHand' ? 22 : layer.motion.startsWith('dust-') ? 1 : layer.motion==='cleanGrip' ? 32 : layout.sailor && layer.motion==='body' ? 52 : layer.motion.startsWith('sheet')||layer.motion==='body'||layer.motion.startsWith('craft')?22:layer.motion==='teaSleeve'?30:9));
    this.original=Array.from(d.vertexCounts,n=>new Float32Array(n*2));
    this.flags=new Uint8Array(d.count);
    this.opacity=new Float32Array(d.count);
    this.orders=new Int32Array(d.count);
    this.ids=Object.fromEntries(p.ids.map((id,i)=>[id,i]));
    this.atlasBase=Math.max(...d.textureIndices)+1+Object.keys(poseFolders).indexOf(name)*16;
    const under=this.slots.filter((_,i)=>layout.layers[i].motion!=='maidBonnet' && (layout.bunnyRepair ? !['bunnyEdge','bunnyGrip'].includes(layout.layers[i].motion) : layout.sharedIdle ? !['hood','earL','earR'].includes(layout.layers[i].motion) : (['body','behindHead','neckListening','earL','earR','cameraSleeveL','cameraSleeveR'].includes(layout.layers[i].motion) || layout.layers[i].motion.startsWith('dust-'))));
    const above=this.slots.filter(i=>!under.includes(i));
    const cover=this.slots[layout.layers.findIndex(layer=>layer.name==='Book Cover')];
    if (name==='Reading' && cover!==undefined) {
      const shadows=this.slots.filter((_,i)=>layout.layers[i].motion.startsWith('shadow'));
      for (const i of shadows) above.splice(above.indexOf(i),1);
      above.splice(above.indexOf(cover),0,...shadows);
    }
    const middle=Array.from(d.renderOrders,(_,i)=>i).filter(i=>!this.slots.includes(i))
      .sort((a,b)=>d.renderOrders[a]-d.renderOrders[b]);
    this.sequence=[...under,...middle,...above];
  }

  static async load(name:string,folder:string,model:CubismModel,renderer:CubismRenderer_WebGL):Promise<StreetPose> {
    const started=performance.now();
    const response=await fetch(folder+'pose.json');
    if(!response.ok)throw Error('Missing '+name+' pose data');
    const layout:Layout=await response.json();
    const images=await Promise.all(layout.layers.map(async layer=>{
      const url=folder+layer.file;
      for(let attempt=0; ;attempt++) {
        try {
          const response=await fetch(url,{cache:attempt===0?'default':'reload'});
          if(!response.ok)throw Error('HTTP '+response.status);
          const source=URL.createObjectURL(await response.blob());
          try {
            const image=new Image();
            image.src=source;
            await image.decode();
            return image;
          } finally {
            URL.revokeObjectURL(source);
          }
        } catch(error) {
          if(attempt===2)throw Error('Could not load '+name+'/'+layer.file+': '+String(error));
          await new Promise(resolve=>setTimeout(resolve,100*(attempt+1)));
        }
      }
    }));
    const gl=renderer.gl;
    const before=gl.getParameter(gl.TEXTURE_BINDING_2D), unpack=gl.getParameter(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL);
    const textures:WebGLTexture[]=[];
    try {
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,true);
      for(const image of images) {
        const texture=gl.createTexture();
        if(!texture)throw Error('Could not allocate pose texture');
        textures.push(texture);
        gl.bindTexture(gl.TEXTURE_2D,texture);
        gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,image);
        gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
      }
      const bytes=images.reduce((size,image)=>size+image.naturalWidth*image.naturalHeight*4,0);
      return new StreetPose(name,model,renderer,layout,textures,bytes,performance.now()-started);
    } catch(error) {
      textures.forEach(texture=>gl.deleteTexture(texture));
      throw error;
    } finally {
      gl.bindTexture(gl.TEXTURE_2D,before);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,unpack);
    }
  }

  start(autoLead?: number):void {
    this.autonomous = autoLead !== undefined;
    this.sawGesture = false;
    this.autonomousDone = false;
    this.time=0;
    this.readingTime=0;
    this.wasTurning=false;
    this.gesture.reset(autoLead);
    this.teaSavor?.reset();
    this.smileGesture?.reset();
    this.maidMotion?.reset(autoLead);
    this.blinkAt=3.4+Math.random()*1.8;
    this.blinkCount=0;
    this.doubleBlink=false;
    this.pose=null;
  }

  dispose():void {
    const bound = this.renderer.getBindedTextures();
    for (let it=bound.begin(); it.notEqual(bound.end());) {
      const slot=it.ptr().first-this.atlasBase;
      if (slot>=0 && slot<this.textures.length) it=bound.erase(it);
      else it.preIncrement();
    }
    this.textures.forEach(texture=>this.renderer.gl.deleteTexture(texture));
  }

  apply(dt:number):void {
    const step=Math.max(0,Math.min(.12,dt));
    this.time+=step;
    const t=this.time;
    if (this.sharedIdle) {
      const values=this.model.getModel().parameters.values;
      this.pose={time:t,breath:values[this.ids.ParamBreath]-.5, gazeX:values[this.ids.ParamEyeBallX],
        gazeY:values[this.ids.ParamEyeBallY],eye:values[this.ids.ParamEyeLOpen],lean:values[this.ids.ParamAngleZ]};
      return;
    }
    const [baseX,baseY,baseGazeX,baseGazeY,baseEye]=this.layout.face;
    const listening=this.name==='ListeningMusic';
    const reading=this.name==='Reading';
    const breath=wave(t,reading?5.3:5.4);
    const elapsed=t-this.blinkAt;
    const blinkInProgress=elapsed>=0 && elapsed<=.2;
    const special=this.gesture.advance(t,listening && blinkInProgress && elapsed>step);
    const interaction=special && this.layout.gesture ? (this.layout.sip
      ? teaDrink(special,this.layout.gesture) : craftStitch(special,this.layout.gesture)) : undefined;
    const savor=this.teaSavor?.sample(t,interaction);
    const turn=reading && special ? pageTurn(special.elapsed,special.duration) : undefined;
    const closing=listening && special && this.layout.eyesClosed;
    const closeAmount=closing ? smooth(special.elapsed/closing.close!) *
      (1-smooth((special.elapsed-closing.close!-special.hold)/closing.open!)) : 0;
    if(this.wasTurning && !turn) this.readingTime=0;
    this.wasTurning=!!turn;
    if(reading && !turn) this.readingTime+=step;
    const scan=reading?readAcrossPage(this.readingTime):null;
    const nextPage=turn ? smooth((turn.elapsed/special.duration-.81)/.19) : 0;
    const gazeMix=turn?.gaze??0;
    const pageFocus=.16-.29*(turn?.phase??0);
    const maid=this.maidMotion?.sample(t,breath);
    if (this.autonomous) {
      const active = !!special || maid?.elapsed != null;
      this.sawGesture ||= active;
      this.autonomousDone = this.name === 'cleaning' ? t >= sweepDuration
        : this.sawGesture && !active && !savor && this.teaSavor?.waiting !== true;
    }
    const hugSqueeze=this.name==='hug' && special ? hugAmount(special.elapsed) : 0;
    const sailor=this.layout.sailor && sailorFrame(this.layout.sailor,special);
    const movement=sailor ? {...sailor,roll:0} : maid ? {gazeX:.14*maid.sweep,gazeY:.39*maid.offer,pitch:3.2*maid.offer,roll:.48*maid.offer+.68*maid.sweep} : actionMotion(this.name,t);
    const gazeX=baseGazeX+movement.gazeX+(interaction?.gazeX??0)+(scan ? scan[0]+(pageFocus-scan[0])*gazeMix+(-.09-scan[0])*nextPage : .032*wave(t,12.1));
    const gazeY=baseGazeY+movement.gazeY+(interaction?.gazeY??0)+(scan ? scan[1]+(-.026-scan[1])*gazeMix-scan[1]*nextPage : .026*wave(t,10.7));
    const lean=listening?1.05*wave(t,6.25)-.42*closeAmount:movement.roll;
    let eye=baseEye;
    if(savor) {
      eye*=1-savor.eyes;
      this.blinkAt=t+1.15;
    } else if(closing) {
      eye*=1-closeAmount;
      this.blinkAt=special.start+special.duration+1.15;
    } else if(elapsed>.2) {
      if(reading&&!this.doubleBlink&&++this.blinkCount%13===0) {
        this.doubleBlink=true;
        this.blinkAt=t+.16;
      } else {
        this.doubleBlink=false;
        this.blinkAt=t+3.3+Math.random()*2.6;
      }
    } else if(elapsed>=0) {
      eye*=elapsed<.087?1-smooth(elapsed/.078):smooth((elapsed-.096)/.1);
    }
    const smileConfig=this.smileGesture && this.layout.sailor?.smile;
    const smile=smileConfig ? phoneSmileAmount(smileConfig,this.smileGesture!.advance(t)) : 0;
    eye*=1-smile*(smileConfig?.eyeNarrowing??0);
    const yawn = this.name==='stretch' ? stretchYawn(t) : undefined;
    if (yawn) eye*=1-yawn.eyeClose;
    eye*=1-.91*hugSqueeze;
    this.pose={time:t,breath,gazeX,gazeY,eye,lean:lean-.72*hugSqueeze,blink:!savor&&!closing&&blinkInProgress};
    if (this.name==='hug') this.pose.hugSqueeze=hugSqueeze;
    if(smileConfig) this.pose.phoneSmile=smile;
    if(interaction)this.pose.interaction=interaction;
    if(savor)this.pose.savor=savor;
    if(sailor)this.pose.sailor=sailor;
    if(maid) this.pose.maid=maid;
    if(turn) this.pose.pageTurn=turn;
    if(closing) this.pose.eyesClosed={elapsed:special.elapsed,hold:special.hold,amount:closeAmount};
    if(scan) {
      this.pose.readingLine=scan[1];
      this.pose.gripDrift=0;
    }
    if(listening) this.pose.cupGripDrift=0;
    const {values,defaultValues}=this.model.getModel().parameters;
    const [followX,followY]=reading?readAcrossPage(Math.max(0,this.readingTime-.24)):[0,0];
    const overrides:Record<string,number>={
      ParamAngleX:baseX+1.15*(maid?.sweep??0)+(reading?4.8*(followX+(-.09-followX)*nextPage):.82*wave(t,6.25)),
      ParamAngleY:baseY+movement.pitch+(interaction?.pitch??0)-1.8*(savor?.nod??0)+(reading?8*followY*(1-nextPage):.45*wave(t,8.4)),ParamAngleZ:0,
      ParamEyeLOpen:eye,ParamEyeROpen:eye,ParamEyeBallX:gazeX,ParamEyeBallY:gazeY,
      ParamEyeLSmile:.88*hugSqueeze,ParamEyeRSmile:.88*hugSqueeze,ParamEyeWetness:0,
      ParamMouthOpenY:yawn?.mouthOpen??.12*hugSqueeze,
      ParamMouthForm:.18*(savor?.smile??0)+(this.layout.mouthForm??0)+smile*(smileConfig?.mouthForm??0)+.76*hugSqueeze,ParamCheek:0,
    };
    for(const key of ['ParamBrowLX','ParamBrowRX','ParamBrowLY','ParamBrowRY','ParamBrowLAngle',
      'ParamBrowRAngle','ParamBrowLForm','ParamBrowRForm']) overrides[key]=defaultValues[this.ids[key]];
    if(savor) {
      for(const key of ['ParamBrowLY','ParamBrowRY']) overrides[key]-=.045*savor.eyes;
    }
    for(const [key,value] of Object.entries(overrides)) {
      if(this.ids[key]>=0)values[this.ids[key]]=value;
    }
  }

  private headPosition(x:number,y:number,pose:Pose):[number,number] {
    const a=(this.layout.roll+pose.lean)*Math.PI/180, c=Math.cos(a),s=Math.sin(a);
    const snuggle=pose.hugSqueeze??0;
    const dx=(x-630)*this.layout.scale,dy=(y-478)*this.layout.scale;
    return [this.layout.anchor[0]+c*dx-s*dy-1.25*snuggle,this.layout.anchor[1]+s*dx+c*dy+2.2*snuggle+(this.sharedIdle ? 0 : this.headLift(pose))];
  }

  private bonnetPosition(x:number,y:number,crown:Float32Array,pose:Pose):[number,number] {
    // Transport the cap using three landmarks of the native crown mesh.
    const across=(x-550.885)/125.615;
    const depth=Math.max(-.25, Math.min(1,(y-103.438)/43.103));
    const left=14*2, right=1*2, center=22*2;
    const dxLeft=600+599.5*crown[left]-550.885;
    const dxRight=600+599.5*crown[right]-676.500;
    const dxCenter=600+599.5*crown[center]-612.461;
    const dyLeft=700-599.5*crown[left+1]-103.438;
    const dyRight=700-599.5*crown[right+1]-103.438;
    const dyCenter=700-599.5*crown[center+1]-146.541;
    const dx=dxLeft+(dxRight-dxLeft)*across + depth*(dxCenter-(dxLeft+dxRight)*.5);
    const dy=dyLeft+(dyRight-dyLeft)*across + depth*(dyCenter-(dyLeft+dyRight)*.5);
    return this.headPosition(x+dx,y+dy,pose);
  }

  private headLift(pose:Pose):number {
    return -(this.name==='Reading'?1.32:1.55)*pose.breath;
  }

  private artPosition(x:number,y:number,kind:string,pose:Pose,layer?:Layer):[number,number] {
    if(this.layout.bunnyRepair) {
      const values=this.model.getModel().parameters.values;
      return bunnyPoint(this.layout,x,y,kind,pose,values[this.ids.ParamAngleX],
        values[this.ids.ParamBodyAngleZ],values[this.ids.ParamHairFront]);
    }
    if(this.sharedIdle) {
      const values=this.model.getModel().parameters.values;
      const yaw=values[this.ids.ParamAngleX], sway=values[this.ids.ParamBodyAngleZ], fringe=values[this.ids.ParamHairFront];
      return outfitPoint(this.layout,x,y,kind,pose,yaw,sway,fringe,layer?.pivot);
    }
    if(pose.sailor && this.layout.sailor) {
      const point=sailorPoint(x,y,kind,pose.breath,pose.sailor,this.layout.sailor);
      if(point) return point;
    }
    if(kind==='teaContact')return [x,y-.52*pose.breath];
    if(this.layout.sip && kind.startsWith('tea'))
      return teaPoint(x,y,kind,pose.breath,pose.interaction,this.layout.sip);
    if(this.layout.stitch && kind.startsWith('craft')) {
      if(kind==='craftHand')return craftPoint(x,y,kind,pose.breath,pose.interaction,this.layout.stitch);
      const [l,t,r,b]=layer!.rect;
      return craftPoint((x-l)/(r-l),(y-t)/(b-t),kind,pose.breath,pose.interaction,this.layout.stitch);
    }
    if(this.layout.gesture && ['hold','handR','hoop','prop'].includes(kind))
      return [x,y-.52*pose.breath];
    if(pose.maid) {
      const moved=maidLayer(this.name,x,y,kind,pose.maid,pose.breath);
      if(moved) return moved;
      if(kind==='lace') return [x+1.2*pose.maid.sweep, y-.22*pose.breath+1.2*pose.maid.offer];
    }
    if(isOutfitAction(this.name) && kind!=='body') {
      return moveLayer(this.name,x,y,kind,pose,layer?.pivot,layer?.strength);
    }
    if(kind==='worn'||kind==='wornHand') {
      const a=pose.lean*Math.PI/180,c=Math.cos(a),s=Math.sin(a);
      const dx=x-this.layout.anchor[0],dy=y-this.layout.anchor[1];
      const weight=kind==='worn' ? 1 : 1-smooth((y-385)/90);
      return [x+((c-1)*dx-s*dy)*weight,y+(s*dx+(c-1)*dy)*weight+this.headLift(pose)];
    }
    if(kind==='body') {
      const [anchorX,anchorY]=this.layout.anchor;
      const neck=(1-smooth((y-anchorY)/86))*(1-smooth((Math.abs(x-anchorX)-57)/54));
      const headTurn=pose.lean*Math.PI/180;
      return [x-(y-anchorY)*headTurn*neck,
        y+this.headLift(pose)*(1-smooth((y-450)/310))+(x-anchorX)*headTurn*neck];
    }
    if(kind==='neckListening') {
      const [ax,ay]=this.layout.anchor;
      const weight=1-smooth((y-ay)/46);
      const a=pose.lean*Math.PI/180;
      return [x-(y-ay)*a*weight,y+this.headLift(pose)+(x-ax)*a*weight];
    }
    const turn=pose.pageTurn;
    if(turn && kind.startsWith('sheet')) {
      const target=pagePoint((x-594)/192,(y-548)/94,turn.phase);
      return [target[0],target[1]-.52*pose.breath];
    }
    if(kind.startsWith('shadow')) return [x,y-.52*pose.breath];
    if(kind==='hold'||kind==='handLeft'||kind==='handRight'||kind==='gripBase'||kind==='gripThumb') {
      const phase=kind==='handLeft'?-.23:.23;
      const isHand=kind!=='hold';
      if(this.name==='Reading') {
        if(!isHand) return [x,y-.52*pose.breath];
        const cuff=smooth(((kind==='handLeft'?408-x:x-780))/53);
        const lift=pose.breath*(-.52-.24*cuff)+.12*cuff*wave(pose.time+phase,5.3);
        if(turn && (kind==='gripBase'||kind==='gripThumb')) {
          const hand=(1-smooth((x-785)/47))*(1-smooth((y-651)/62))*turn.reach;
          if(kind==='gripThumb') {
            const corner=pagePoint(1,.91,Math.min(.09,turn.phase));
            const tip=(1-smooth((x-736)/54))*(1-smooth((y-642)/28))*turn.pinch;
            return [x+5*hand+(corner[0]-743)*tip,
              y+lift-9*hand+(corner[1]-625)*tip];
          }
          const index=(1-smooth((x-720)/74))*Math.exp(-(((y-671)/15)**2))*turn.pinch;
          return [x+5*hand+8*index,y+lift-9*hand-10*index];
        }
        return [x,y+lift];
      }
      const flex=isHand?.2*wave(pose.time+phase,6.2):0;
      return [x+flex,y-.65*pose.breath+Math.abs(flex)];
    }
    return [x,y];
  }

  private place(points:Float32Array,i:number,x:number,y:number):void {
    // Invert the artwork calibration around the same neck landmark used by headPosition.
    // Applying it to every layer keeps contact points and joint proportions intact.
    points[i]=(630-600)/599.5+(x-this.layout.anchor[0])/this.modelPixels;
    points[i+1]=(700-478)/599.5+(this.layout.anchor[1]-y)/this.modelPixels;
  }

  draw():void {
    if(!this.pose)return;
    const pose=this.pose;
    const d=this.model.getModel().drawables;
    const clipping = this.layout.sailor || this.layout.bunnyRepair || this.name==='cleaning' || this.layout.layers.some(layer=>layer.motion==='maidBonnet')
      ? this.renderer._clippingManager?.getClippingContextListForDraw() : null;
    const unmasked = clipping ? this.slots.flatMap((slot,index)=>{
      const kind=this.layout.layers[index].motion;
      return kind==='maidBonnet' || this.layout.sailor || this.layout.bunnyRepair || kind==='cleanGrip' || kind.startsWith('dust-') ? [[slot,clipping.at(slot)] as const] : [];
    }) : [];
    const saved=this.slots.map(i=>({
      xy:d.vertexPositions[i],uv:d.vertexUvs[i],indices:d.indices[i],
      count:d.vertexCounts[i],indexCount:d.indexCounts[i],tex:d.textureIndices[i],flags:d.constantFlags[i],
    }));
    this.flags.set(d.dynamicFlags);
    this.opacity.set(d.opacities);
    this.orders.set(d.renderOrders);
    try {
      // Extend the small native pupil travel while retaining the original eyelid masks.
      const irisDrop=(this.layout.irisDownPixels??0)*Math.max(0,-pose.gazeY)*smooth(pose.eye/.70);
      for(let i=0;i<d.count;i++) {
        const v=d.vertexPositions[i];
        const pupilOffset=this.irises.has(i) ? irisDrop : 0;
        this.original[i].set(v);
        if(this.body.has(i)) {
          d.opacities[i]=0;
          d.dynamicFlags[i]&=~visible;
        } else {
          d.dynamicFlags[i]|=dirty;
          for(let j=0;j<v.length;j+=2) {
            const x=600+v[j]*599.5,y=700-v[j+1]*599.5+pupilOffset;
            const target=this.headPosition(x,y,pose);
            if(!this.sharedIdle && this.hairTips.has(i)) {
              const tip=smooth((y-380)/140);
              const listening=this.name==='ListeningMusic';
              const lag=listening?wave(pose.time-.21,6.25)-wave(pose.time,6.25):wave(pose.time-.26,5.8);
              target[0]+=tip*lag*(listening?1.25:.36);
            }
            this.place(v,j,target[0],target[1]);
          }
        }
      }
      this.sequence.forEach((i,rank)=>{d.renderOrders[i]=rank;});
      for(const [index,i] of this.slots.entries()) {
        const shape=this.shapes[index], layer=this.layout.layers[index];
        const motion=layer.motion, turn=pose.pageTurn;
        let alpha=pose.sailor ? sailorOpacity(motion,pose.sailor) : motion.startsWith('dust-') ? pose.maid?.dust[Number(motion.slice(5))]?.alpha ?? 0 : 1;
        if(motion==='teaContact')alpha=1-smooth((pose.interaction?.amount??0)/.18);
        if(motion==='handRight' && turn) alpha=0;
        if(motion.startsWith('grip')) alpha=turn?1:0;
        if(motion.startsWith('shadow')) alpha=turn ? turn.shadow *
          (motion==='shadowRight'?1-smooth((turn.phase-.32)/.40):smooth((turn.phase-.25)/.45)) : 0;
        if(motion.startsWith('sheet')) {
          const phase=turn?.phase??0;
          const shown=smooth(phase/.035)*(1-smooth((phase-.96)/.04));
          const reverse=smooth((phase-.44)/.12);
          if(motion==='sheetEdge') alpha=shown*.65;
          else if(motion==='sheetFront') alpha=reverse>=1?0:shown;
          else alpha=shown*reverse;
          // The curved sheet can show both orientations within the same frame.
          if (alpha>0) d.constantFlags[i]|=4;
        }
        if(alpha<=0) continue;
        if(pose.sailor) d.constantFlags[i]=4;
        // Replacement quads use normal alpha, independent of the donor mesh's blend mode.
        if(motion==='cleanGrip' || motion.startsWith('dust-')) d.constantFlags[i]=4;
        if(pose.cupGripDrift!==undefined&&layer.motion==='wornHand') {
          const hand=this.artPosition(770,301,'wornHand',pose), cup=this.artPosition(770,301,'worn',pose);
          pose.cupGripDrift=Math.hypot(hand[0]-cup[0],hand[1]-cup[1]);
        }
        if(pose.gripDrift!==undefined&&(layer.motion==='handLeft'||layer.motion==='handRight')) {
          const x=layer.motion==='handLeft'?429:754;
          const y=653;
          const hand=this.artPosition(x,y,layer.motion,pose), cover=this.artPosition(x,y,'hold',pose);
          pose.gripDrift=Math.max(pose.gripDrift,Math.hypot(hand[0]-cover[0],hand[1]-cover[1]));
        }
        for(let j=0;j<shape.points.length;j+=2) {
          const x=shape.points[j],y=shape.points[j+1];
          const target=motion==='maidBonnet'
            ? this.bonnetPosition(x,y,this.original[this.crown],pose)
            : this.artPosition(x,y,motion,pose,layer);
          this.place(shape.xy,j,target[0],target[1]);
          // The prop, grip, sleeve and body must use the same post-motion field.
          composeBodyVertex(shape.xy,j,this.composition);
        }
        if(this.layout.bunnyRepair || motion==='maidBonnet') d.constantFlags[i]=4;
        d.vertexPositions[i]=shape.xy;
        d.vertexUvs[i]=shape.uv;
        d.indices[i]=shape.indices;
        d.vertexCounts[i]=shape.xy.length/2;
        d.indexCounts[i]=shape.indices.length;
        d.textureIndices[i]=this.atlasBase+index;
        d.opacities[i]=alpha;
        d.dynamicFlags[i]|=dirty|visible;
        this.renderer.bindTexture(this.atlasBase+index,this.textures[index]);
      }
      for (const [slot] of unmasked) clipping!.set(slot,null);
      this.renderer.drawModel();
    } finally {
      for (const [slot,context] of unmasked) clipping!.set(slot,context);
      this.slots.forEach((i,index)=>{
        const slot=saved[index];
        d.vertexPositions[i]=slot.xy; d.vertexUvs[i]=slot.uv; d.indices[i]=slot.indices;
        d.vertexCounts[i]=slot.count; d.indexCounts[i]=slot.indexCount; d.textureIndices[i]=slot.tex;
        d.constantFlags[i]=slot.flags;
      });
      d.vertexPositions.forEach((v,i)=>v.set(this.original[i]));
      d.opacities.set(this.opacity);
      d.dynamicFlags.set(this.flags);
      d.renderOrders.set(this.orders);
    }
  }
}

function grid(rect:Rect, cells:number) {
  const stride=cells+1,count=stride*stride;
  const points=new Float32Array(count*2),xy=new Float32Array(count*2),uv=new Float32Array(count*2);
  const indices=new Uint16Array(cells*cells*6);
  let k=0;
  for(let row=0;row<stride;row++) for(let col=0;col<stride;col++) {
    const i=row*stride+col,u=col/cells,v=row/cells;
    points.set([rect[0]+u*(rect[2]-rect[0]),rect[1]+v*(rect[3]-rect[1])],i*2);
    uv.set([u,1-v],i*2);
    if(row<cells&&col<cells) {
      indices.set([i,i+stride,i+1,i+1,i+stride,i+stride+1],k);
      k+=6;
    }
  }
  return {points,xy,uv,indices};
}

function outfitPoint(layout:Layout,x:number,y:number,kind:string,pose:Pose,yaw:number,sway:number,
  fringe:number,pivot?:[number,number]):[number,number] {
  // Map the reference idle offsets into registered outfit coordinates, keeping shoes planted.
  const [ax,ay]=layout.anchor;
  const scale=layout.scale*599.5;
  const baseY=(700-(478+(y-ay)/layout.scale))/599.5;
  const headSway=0.00225*sway*smooth((baseY+.76)/1.7)*scale;
  const breathing=smooth((baseY+.70)/.95)*pose.breath;
  const taper=1-smooth((y-520)/280);
  const dx=(headSway+.002*breathing*scale)*taper;
  const dy=-.012*breathing*scale*taper;
  if(kind==='neck') {
    const weight=1-smooth((y-ay+14)/66);
    return [x+dx+yaw*.37*weight, y+dy+(x-ax)*.008*pose.lean*weight];
  }
  if(kind==='hood'||kind==='earL'||kind==='earR') {
    const [px,py]=pivot??[ax,ay];
    const settle=1-smooth((y-ay+18)/102);
    const turn=yaw*.42*settle;
    const angle=pivot?(fringe*.76 + pose.lean*.55)*Math.PI/180:0;
    const c=Math.cos(angle),s=Math.sin(angle);
    return [px+(x-px)*c-(y-py)*s+dx+turn, py+(x-px)*s+(y-py)*c+dy];
  }
  if(kind==='fixed')return [x,y];
  if(kind==='skirt')return [x+.21*fringe*smooth((y-820)/440),y];
  return [x+dx+(kind==='trim'?.19*fringe:0),y+dy];
}

const outfitActions = new Set(['tea','handcraft','serving','cleaning','hug','stretch','phone','photo','bunnyRest']);
const isOutfitAction = (name:string) => outfitActions.has(name);

function pulse(t:number, period:number, start:number, rise:number, hold:number, fall:number):number {
  const local = t % period - start;
  return smooth(local / rise) * (1 - smooth((local - rise - hold) / fall));
}

const HUG_RISE=.72, HUG_HOLD=1.35, HUG_RELEASE=.96;

function hugAmount(elapsed:number) {
  return smooth(elapsed/HUG_RISE)*(1-smooth((elapsed-HUG_RISE-HUG_HOLD)/HUG_RELEASE));
}

// Use the same reach envelope for the sleeves, head, eyelids and mouth.
function stretchYawn(t:number) {
  const reach=pulse(t,7.3,.28,1.05,.8,2.15);
  return {
    reach,
    eyeClose:smooth((reach-.08)/.92),
    mouthOpen:0.78*smooth((reach-.20)/.80),
  };
}

function actionMotion(name:string, t:number) {
  const neutral = {lift:0, shift:0, roll:0, pitch:0, gazeX:0, gazeY:0, detail:0};
  switch(name) {
    case 'serving': {
      const present = pulse(t, 9.8, 2.1, 1.6, 1.55, 1.8);
      return {...neutral, lift:2.8*present, shift:.7*present, pitch:-.8*present};
    }
    case 'cleaning': {
      const sweep = wave(t,5.3);
      return {...neutral, shift:1.4*sweep, roll:-.42*sweep, gazeX:.055*sweep, detail:sweep};
    }
    case 'stretch': {
      const {reach}=stretchYawn(t);
      return {...neutral, lift:5.3*reach, pitch:2.35*reach, gazeY:.07*reach, detail:reach};
    }
    default: return neutral;
  }
}

function moveLayer(name:string,x:number,y:number,kind:string,pose:Pose,pivot?:[number,number],strength=1):[number,number] {
  const motion = actionMotion(name,pose.time);
  const breath = -.52*pose.breath;
  if (kind==='skirt') return [x + .26*wave(pose.time,5.4), y];
  if (kind==='apron' || kind==='lace') return [x+.42*wave(pose.time-.2,5.4), y+breath*.42];
  if (kind==='behindHead' || kind==='frontHood') return [x,y-1.55*pose.breath];
  if (kind==='earL' || kind==='earR') {
    const [px,py]=pivot??[x,y];
    const lag = wave(pose.time-(kind==='earL'?.27:.41),4.2);
    const degrees = (kind==='earL'?1:-1)*(.65*lag+1.25*motion.detail*(name==='stretch'?1:0));
    return rotate(x,y,px,py,degrees,0,-1.55*pose.breath);
  }
  if (kind==='duster') {
    const [px,py]=pivot??[x,y];
    return rotate(x,y,px,py,4.4*motion.detail,motion.shift,breath);
  }
  if (kind==='armL' || kind==='armR' || kind==='handL' || kind==='handR' || kind==='finger') {
    const [px,py]=pivot??[x,y];
    const side=kind.endsWith('L')?1:-1;
    const reach=name==='stretch'&&kind.startsWith('arm') ? smooth((py-y)/145) : 1;
    const flex=name==='hug'?side*1.35*motion.detail:0;
    const move=name==='handcraft'&&kind.endsWith('R') || name==='cleaning'&&kind.endsWith('R') ? 0 : 1;
    const press=kind==='finger'?(name==='handcraft'?.95:1.35)*motion.detail:0;
    const turn=kind==='finger' ? .85*motion.detail : name==='cleaning'&&move ? 1.8*motion.detail : 0;
    return rotate(x,y,px,py,turn,motion.shift*move+flex+press*.48,
      breath-motion.lift*move*reach*strength+press);
  }
  if (kind==='hoop' || kind==='rest') return [x,y+breath];
  return [x+motion.shift,y+breath-motion.lift*strength];
}

function rotate(x:number,y:number,px:number,py:number,deg:number,dx:number,dy:number):[number,number] {
  const a=deg*Math.PI/180,c=Math.cos(a),s=Math.sin(a);
  return [px+(x-px)*c-(y-py)*s+dx,py+(x-px)*s+(y-py)*c+dy];
}

type BunnyRepair = { leftEar:[number,number]; rightEar:[number,number] };

function bunnyPoint(layout:Layout, x:number, y:number, kind:string, pose:Pose,
  yaw:number, sway:number, fringe:number):[number,number] {
  const [ax,ay]=layout.anchor;
  const stretch=layout.id==='stretch';
  const reach=stretch?actionMotion('stretch',pose.time).detail:0;
  const squeeze=pose.hugSqueeze??0;
  const taper=1-smooth((y-475)/380);
  const body=layout.sharedIdle ? outfitPoint(layout,x,y,'body',pose,yaw,sway,fringe) :
    [x,y-1.55*pose.breath*taper];
  const follow=1-smooth((y-ay+30)/123);
  const turn=yaw*.32*follow;
  const upper=stretch?(1-smooth((y-75)/430))*smooth((Math.abs(x-ax)-145)/260):0;
  const cuffLift=-8*reach*upper;
  if(layout.id==='hug' && squeeze && (kind==='bunnyGrip'||kind==='bunnyBody')) {
    // Apply the identical coordinate field to the grip and garment layers.
    const contact=smooth((y-390)/115)*(1-smooth((y-617)/139))*
      (1-smooth((Math.abs(x-542)-138)/155));
    body[0]+=(542-x)*.052*contact*squeeze;
    body[1]-=6.1*contact*squeeze;
    if(kind==='bunnyGrip')return [body[0],body[1]];
  }
  if(kind==='bunnyGrip')return [body[0],body[1]];
  if(kind==='bunnyNeck') {
    const root=1-smooth((y-ay+13)/63);
    return [body[0]+yaw*.43*root,body[1]+(x-ax)*.009*pose.lean*root];
  }
  const [earL,earR]=[layout.bunnyRepair!.leftEar,layout.bunnyRepair!.rightEar];
  const isLeft=x<ax;
  const [px,py]=isLeft?earL:earR;
  const away=(isLeft ? px-x : x-px);
  const earWeight=smooth((away-8)/92)*(1-smooth((y-(stretch?248:235))/105));
  const lag=layout.sharedIdle?fringe:wave(pose.time-(isLeft?.29:.45),4.4);
  const angle=(isLeft?1:-1)*(.65*lag+.85*reach)*earWeight*Math.PI/180;
  const c=Math.cos(angle),s=Math.sin(angle);
  const lift=-2.4*reach*follow-1.25*squeeze*follow+cuffLift;
  return [body[0]+turn+(c-1)*(x-px)-s*(y-py),body[1]+lift+s*(x-px)+(c-1)*(y-py)];
}
