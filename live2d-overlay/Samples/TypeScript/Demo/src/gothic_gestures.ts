import { ease, GestureConfig, PoseGesture } from './street_pose_gestures';

type Point = [number,number];
export type SipRig = {lift:number;shift:number;tilt:number;pivot:Point;restDrop?:number};
export type StitchRig = {grip:Point;tip:Point;work:Point;threadEnd:Point};
type Event = NonNullable<ReturnType<PoseGesture['advance']>>;
export type Interaction = {start:number; elapsed:number; duration:number; hold:number; count:number; phase:string;
  amount:number; progress:number; stitch:number; offset:Point; pitch:number; gazeX:number; gazeY:number};

export class TeaSavor {
  private drinkEnd = -Infinity;
  private begins = -Infinity;

  get waiting(): boolean {
    return this.drinkEnd !== -Infinity || this.begins !== -Infinity;
  }

  reset(): void {
    this.drinkEnd = -Infinity;
    this.begins = -Infinity;
  }

  sample(time:number, drink: Interaction | undefined) {
    if (drink) {
      this.drinkEnd = drink.start + drink.duration;
      this.begins = -Infinity;
      return;
    }
    if (time >= this.drinkEnd && this.drinkEnd !== -Infinity) {
      this.begins = Math.random() < .6 ? this.drinkEnd + .12 : -Infinity;
      this.drinkEnd = -Infinity;
    }
    const elapsed = time - this.begins;
    const close = .48, hold = 1.35, open = .60;
    if (elapsed >= close + hold + open) this.begins = -Infinity;
    if (elapsed < 0 || elapsed >= close + hold + open) return;
    const eyes = ease(elapsed / close) * (1 - ease((elapsed - close - hold) / open));
    const smile = ease((elapsed - .18) / .55) * (1 - ease((elapsed - 1.62) / .78));
    const nod = ease((elapsed - .35) / .56) * (1 - ease((elapsed - 1.13) / .74));
    return {elapsed, eyes, smile, nod};
  }
}

export function teaDrink(event:Event, config:GestureConfig):Interaction {
  const {elapsed,hold}=event, raise=config.close!, lower=config.open!;
  const end=raise+hold;
  const amount=ease(elapsed/raise)*(1-ease((elapsed-end)/lower));
  return {...event,phase:elapsed<raise?'raise':elapsed<end?'sip':'lower',
    amount,progress:0,stitch:0,offset:[0,0],pitch:-2.1*amount,gazeX:.085*amount,gazeY:.24*amount};
}

export function craftStitch(event:Event, config:GestureConfig):Interaction {
  const {elapsed,count}=event, len=config.secondsPerCount!;
  const at=elapsed-config.close!, work=count*len;
  const stitch=Math.min(count-1,Math.max(0,Math.floor(at/len)));
  const progress=Math.max(0,Math.min(1,(at-stitch*len)/len));
  const amount=ease(progress/.46)*(1-ease((progress-.61)/.39));
  const inspect=ease((at-work)/.32)*(1-ease((elapsed-event.duration+.34)/.34));
  // Stitches alternate between nearby punctures on the original embroidery.
  return {...event,phase:at<0?'prepare':at<work?'stitch':'inspect',
    amount,progress,stitch,offset:[(stitch-1)*3.2*amount,(stitch%2)*1.7*amount],
    pitch:-.75*amount-.5*inspect,gazeX:-.055*amount-.018*inspect,gazeY:-.055*amount-.025*inspect};
}

export function teaPoint(x:number,y:number,kind:string,breath:number,action:Interaction|undefined,rig:SipRig):Point {
  const m=action?.amount??0,[px,py]=rig.pivot;
  const a=rig.tilt*m*Math.PI/180,c=Math.cos(a),s=Math.sin(a);
  const dx=px+(x-px)*c-(y-py)*s+rig.shift*m-x;
  const dy=py+(x-px)*s+(y-py)*c-rig.lift*m-y+(rig.restDrop??0)*(1-m);
  const weight=kind==='teaSleeve'?1-ease((y-601)/87):1;
  return [x+weight*dx,y-.52*breath+weight*dy];
}

const mix=(a:number,b:number,w:number)=>a+(b-a)*w;
export function craftPoint(u:number,v:number,kind:string,breath:number,action:Interaction|undefined,rig:StitchRig):Point {
  const amount=action?.amount??0, [gx,gy]=rig.grip, [tx,ty]=rig.tip, [wx,wy]=rig.work;
  const [ox,oy]=action?.offset??[0,0];
  const dx=(wx-tx+3)*amount+ox, dy=(wy-ty+4)*amount+oy;
  const grip:Point=[gx+dx,gy+dy];
  const tip:Point=[tx+dx,ty+dy];
  const width=kind==='craftThread'?1.85:1.5;
  const p=v*width-width/2;
  if(kind==='craftHand'){
    const weight=1-ease((v-522)/63);
    return [u+dx*weight,v+dy*weight-.52*breath];
  }
  if(kind==='craftNeedle'){
    // Project its exposed length onto the cloth plane. The hidden end stays registered to the grip.
    const visible=1-.13*ease((amount-.73)/.27);
    return [mix(grip[0],tip[0],u*visible)-.67*p,mix(grip[1],tip[1],u*visible)+.74*p-.52*breath];
  }
  const [ex,ey]=rig.threadEnd, k=1-u;
  const endX=mix(ex,wx,amount)+ox, endY=mix(ey,wy,amount)+oy;
  const cx=grip[0]-19*(1-amount)+3,cy=grip[1]+30;
  const endControlX=endX-26*(1-amount),endControlY=endY+2-3*amount;
  return [k*k*k*grip[0]+3*k*k*u*cx+3*k*u*u*endControlX+u*u*u*endX+p,
    k*k*k*grip[1]+3*k*k*u*cy+3*k*u*u*endControlY+u*u*u*endY+p*.34-.52*breath];
}
