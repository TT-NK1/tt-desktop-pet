import { PoseCache, outfitPoses, transitionTime } from "./desktop_switch";
import { FaceRig } from './face_rig';
import { StreetIdle } from './street_idle';
import { StreetPose, poseFolders } from './street_poses';
/**
 * Copyright(c) Live2D Inc. All rights reserved.
 *
 * Use of this source code is governed by the Live2D Open Software license
 * that can be found at https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html.
 */

import { CubismDefaultParameterId } from '@framework/cubismdefaultparameterid';
import { CubismModelSettingJson } from '@framework/cubismmodelsettingjson';
import {
  BreathParameterData,
  CubismBreath
} from '@framework/effect/cubismbreath';
import { CubismEyeBlink } from '@framework/effect/cubismeyeblink';
import { ICubismModelSetting } from '@framework/icubismmodelsetting';
import { CubismIdHandle } from '@framework/id/cubismid';
import { CubismFramework } from '@framework/live2dcubismframework';
import { CubismMatrix44 } from '@framework/math/cubismmatrix44';
import { CubismUserModel } from '@framework/model/cubismusermodel';
import {
  ACubismMotion,
  BeganMotionCallback,
  FinishedMotionCallback
} from '@framework/motion/acubismmotion';
import { CubismMotion } from '@framework/motion/cubismmotion';
import { CubismExpressionMotion, ExpressionBlendType } from '@framework/motion/cubismexpressionmotion';
import {
  CubismMotionQueueEntryHandle,
  InvalidMotionQueueEntryHandleValue
} from '@framework/motion/cubismmotionqueuemanager';
import { csmMap } from '@framework/type/csmmap';
import { csmRect } from '@framework/type/csmrectf';
import { csmString } from '@framework/type/csmstring';
import { csmVector } from '@framework/type/csmvector';
import {
  CSM_ASSERT,
  CubismLogError,
  CubismLogInfo
} from '@framework/utils/cubismdebug';

import * as LAppDefine from './lappdefine';
import { LAppPal } from './lapppal';
import { TextureInfo } from './lapptexturemanager';
import { LAppWavFileHandler } from './lappwavfilehandler';
import { CubismMoc } from '@framework/model/cubismmoc';
import { LAppDelegate } from './lappdelegate';
import { LAppSubdelegate } from './lappsubdelegate';

enum LoadStep {
  LoadAssets,
  LoadModel,
  WaitLoadModel,
  LoadExpression,
  WaitLoadExpression,
  LoadPhysics,
  WaitLoadPhysics,
  LoadPose,
  WaitLoadPose,
  SetupEyeBlink,
  SetupBreath,
  LoadUserData,
  WaitLoadUserData,
  SetupEyeBlinkIds,
  SetupLipSyncIds,
  SetupLayout,
  LoadMotion,
  WaitLoadMotion,
  CompleteInitialize,
  CompleteSetupModel,
  LoadTexture,
  WaitLoadTexture,
  CompleteSetup
}

/**
 * ユーザーが実際に使用するモデルの実装クラス<br>
 * モデル生成、機能コンポーネント生成、更新処理とレンダリングの呼び出しを行う。
 */
import { portraitMode, incomingFrame, mirrorStats, portraitExpression } from './character_state';

const desktopMode = new URLSearchParams(location.search).has('desktop');

export class LAppModel extends CubismUserModel {
  /**
   * model3.jsonが置かれたディレクトリとファイルパスからモデルを生成する
   * @param dir
   * @param fileName
   */
  public loadAssets(dir: string, fileName: string): void {
    this._modelHomeDir = dir;
    this.modelStarted = performance.now();

    fetch(`${this._modelHomeDir}${fileName}`)
      .then(response => response.arrayBuffer())
      .then(arrayBuffer => {
        const setting: ICubismModelSetting = new CubismModelSettingJson(
          arrayBuffer,
          arrayBuffer.byteLength
        );

        // ステートを更新
        this._state = LoadStep.LoadModel;

        // 結果を保存
        this.setupModel(setting);
      })
      .catch(error => {
        // model3.json読み込みでエラーが発生した時点で描画は不可能なので、setupせずエラーをcatchして何もしない
        CubismLogError(`Failed to load file ${this._modelHomeDir}${fileName}`);
      });
  }

  /**
   * model3.jsonからモデルを生成する。
   * model3.jsonの記述に従ってモデル生成、モーション、物理演算などのコンポーネント生成を行う。
   *
   * @param setting ICubismModelSettingのインスタンス
   */
  private setupModel(setting: ICubismModelSetting): void {
    this._updating = true;
    this._initialized = false;

    this._modelSetting = setting;

    // CubismModel
    if (this._modelSetting.getModelFileName() != '') {
      const modelFileName = this._modelSetting.getModelFileName();

      fetch(`${this._modelHomeDir}${modelFileName}`)
        .then(response => {
          if (response.ok) {
            return response.arrayBuffer();
          } else if (response.status >= 400) {
            CubismLogError(
              `Failed to load file ${this._modelHomeDir}${modelFileName}`
            );
            return new ArrayBuffer(0);
          }
        })
        .then(arrayBuffer => {
          this.loadModel(arrayBuffer, this._mocConsistency);
          this.modelMs = performance.now() - this.modelStarted;
          this._state = LoadStep.LoadExpression;

          // callback
          loadCubismExpression();
        });

      this._state = LoadStep.WaitLoadModel;
    } else {
      LAppPal.printMessage('Model data does not exist.');
    }

    // Expression
    const loadCubismExpression = (): void => {
      if (this._modelSetting.getExpressionCount() > 0) {
        const count: number = this._modelSetting.getExpressionCount();

        for (let i = 0; i < count; i++) {
          const expressionName = this._modelSetting.getExpressionName(i);
          const expressionFileName =
            this._modelSetting.getExpressionFileName(i);

          fetch(`${this._modelHomeDir}${expressionFileName}`)
            .then(response => {
              if (response.ok) {
                return response.arrayBuffer();
              } else if (response.status >= 400) {
                CubismLogError(
                  `Failed to load file ${this._modelHomeDir}${expressionFileName}`
                );
                // ファイルが存在しなくてもresponseはnullを返却しないため、空のArrayBufferで対応する
                return new ArrayBuffer(0);
              }
            })
            .then(arrayBuffer => {
              const motion: ACubismMotion = this.loadExpression(
                arrayBuffer,
                arrayBuffer.byteLength,
                expressionName
              );

              if (this._expressions.getValue(expressionName) != null) {
                ACubismMotion.delete(
                  this._expressions.getValue(expressionName)
                );
                this._expressions.setValue(expressionName, null);
              }

              this._expressions.setValue(expressionName, motion);

              this._expressionCount++;

              if (this._expressionCount >= count) {
                this._state = LoadStep.LoadPhysics;

                // callback
                loadCubismPhysics();
              }
            });
        }
        this._state = LoadStep.WaitLoadExpression;
      } else {
        this._state = LoadStep.LoadPhysics;

        // callback
        loadCubismPhysics();
      }
    };

    // Physics
    const loadCubismPhysics = (): void => {
      if (this._modelSetting.getPhysicsFileName() != '') {
        const physicsFileName = this._modelSetting.getPhysicsFileName();

        fetch(`${this._modelHomeDir}${physicsFileName}`)
          .then(response => {
            if (response.ok) {
              return response.arrayBuffer();
            } else if (response.status >= 400) {
              CubismLogError(
                `Failed to load file ${this._modelHomeDir}${physicsFileName}`
              );
              return new ArrayBuffer(0);
            }
          })
          .then(arrayBuffer => {
            this.loadPhysics(arrayBuffer, arrayBuffer.byteLength);

            this._state = LoadStep.LoadPose;

            // callback
            loadCubismPose();
          });
        this._state = LoadStep.WaitLoadPhysics;
      } else {
        this._state = LoadStep.LoadPose;

        // callback
        loadCubismPose();
      }
    };

    // Pose
    const loadCubismPose = (): void => {
      if (this._modelSetting.getPoseFileName() != '') {
        const poseFileName = this._modelSetting.getPoseFileName();

        fetch(`${this._modelHomeDir}${poseFileName}`)
          .then(response => {
            if (response.ok) {
              return response.arrayBuffer();
            } else if (response.status >= 400) {
              CubismLogError(
                `Failed to load file ${this._modelHomeDir}${poseFileName}`
              );
              return new ArrayBuffer(0);
            }
          })
          .then(arrayBuffer => {
            this.loadPose(arrayBuffer, arrayBuffer.byteLength);

            this._state = LoadStep.SetupEyeBlink;

            // callback
            setupEyeBlink();
          });
        this._state = LoadStep.WaitLoadPose;
      } else {
        this._state = LoadStep.SetupEyeBlink;

        // callback
        setupEyeBlink();
      }
    };

    // EyeBlink
    const setupEyeBlink = (): void => {
      if (this._modelSetting.getEyeBlinkParameterCount() > 0) {
        this._eyeBlink = CubismEyeBlink.create(this._modelSetting);
        this._state = LoadStep.SetupBreath;
      }

      // callback
      setupBreath();
    };

    // Breath
    const setupBreath = (): void => {
      this._breath = CubismBreath.create();

      const breathParameters: csmVector<BreathParameterData> = new csmVector();
      breathParameters.pushBack(
        new BreathParameterData(this._idParamAngleX, 0.0, 15.0, 6.5345, 0.5)
      );
      breathParameters.pushBack(
        new BreathParameterData(this._idParamAngleY, 0.0, 8.0, 3.5345, 0.5)
      );
      breathParameters.pushBack(
        new BreathParameterData(this._idParamAngleZ, 0.0, 10.0, 5.5345, 0.5)
      );
      breathParameters.pushBack(
        new BreathParameterData(this._idParamBodyAngleX, 0.0, 4.0, 15.5345, 0.5)
      );
      breathParameters.pushBack(
        new BreathParameterData(
          CubismFramework.getIdManager().getId(
            CubismDefaultParameterId.ParamBreath
          ),
          0.5,
          0.5,
          3.2345,
          1
        )
      );

      this._breath.setParameters(breathParameters);
      this._state = LoadStep.LoadUserData;

      // callback
      loadUserData();
    };

    // UserData
    const loadUserData = (): void => {
      if (this._modelSetting.getUserDataFile() != '') {
        const userDataFile = this._modelSetting.getUserDataFile();

        fetch(`${this._modelHomeDir}${userDataFile}`)
          .then(response => {
            if (response.ok) {
              return response.arrayBuffer();
            } else if (response.status >= 400) {
              CubismLogError(
                `Failed to load file ${this._modelHomeDir}${userDataFile}`
              );
              return new ArrayBuffer(0);
            }
          })
          .then(arrayBuffer => {
            this.loadUserData(arrayBuffer, arrayBuffer.byteLength);

            this._state = LoadStep.SetupEyeBlinkIds;

            // callback
            setupEyeBlinkIds();
          });

        this._state = LoadStep.WaitLoadUserData;
      } else {
        this._state = LoadStep.SetupEyeBlinkIds;

        // callback
        setupEyeBlinkIds();
      }
    };

    // EyeBlinkIds
    const setupEyeBlinkIds = (): void => {
      const eyeBlinkIdCount: number =
        this._modelSetting.getEyeBlinkParameterCount();

      for (let i = 0; i < eyeBlinkIdCount; ++i) {
        this._eyeBlinkIds.pushBack(
          this._modelSetting.getEyeBlinkParameterId(i)
        );
      }

      this._state = LoadStep.SetupLipSyncIds;

      // callback
      setupLipSyncIds();
    };

    // LipSyncIds
    const setupLipSyncIds = (): void => {
      const lipSyncIdCount = this._modelSetting.getLipSyncParameterCount();

      for (let i = 0; i < lipSyncIdCount; ++i) {
        this._lipSyncIds.pushBack(this._modelSetting.getLipSyncParameterId(i));
      }
      this._state = LoadStep.SetupLayout;

      // callback
      setupLayout();
    };

    // Layout
    const setupLayout = (): void => {
      const layout: csmMap<string, number> = new csmMap<string, number>();

      if (this._modelSetting == null || this._modelMatrix == null) {
        CubismLogError('Failed to setupLayout().');
        return;
      }

      this._modelSetting.getLayoutMap(layout);
      this._modelMatrix.setupFromLayout(layout);
      this._state = LoadStep.LoadMotion;

      // callback
      loadCubismMotion();
    };

    // Motion
    const loadCubismMotion = (): void => {
      this._state = LoadStep.WaitLoadMotion;
      this._model.saveParameters();
      this._allMotionCount = 0;
      this._motionCount = 0;
      const group: string[] = [];

      const motionGroupCount: number = this._modelSetting.getMotionGroupCount();

      // モーションの総数を求める
      for (let i = 0; i < motionGroupCount; i++) {
        group[i] = this._modelSetting.getMotionGroupName(i);
        this._allMotionCount += this._modelSetting.getMotionCount(group[i]);
      }

      // モーションの読み込み
      for (let i = 0; i < motionGroupCount; i++) {
        this.preLoadMotionGroup(group[i]);
      }

      // モーションがない場合
      if (motionGroupCount == 0) {
        this._state = LoadStep.LoadTexture;

        // 全てのモーションを停止する
        this._motionManager.stopAllMotions();

        this._updating = false;
        this._initialized = true;

        this.createRenderer();
        this.setupTextures();
        this.getRenderer().startUp(this._subdelegate.getGlManager().getGl());
      }
    };
  }

  /**
   * テクスチャユニットにテクスチャをロードする
   */
  private setupTextures(): void {
    // iPhoneでのアルファ品質向上のためTypescriptではpremultipliedAlphaを採用
    const usePremultiply = true;

    if (this._state == LoadStep.LoadTexture) {
      // テクスチャ読み込み用
      const texturesStarted = performance.now();
      const textureCount: number = this._modelSetting.getTextureCount();

      for (
        let modelTextureNumber = 0;
        modelTextureNumber < textureCount;
        modelTextureNumber++
      ) {
        // テクスチャ名が空文字だった場合はロード・バインド処理をスキップ
        if (this._modelSetting.getTextureFileName(modelTextureNumber) == '') {
          console.log('getTextureFileName null');
          continue;
        }

        // WebGLのテクスチャユニットにテクスチャをロードする
        let texturePath =
          this._modelSetting.getTextureFileName(modelTextureNumber);
        texturePath = this._modelHomeDir + texturePath;

        // ロード完了時に呼び出すコールバック関数
        const onLoad = (textureInfo: TextureInfo): void => {
          this.getRenderer().bindTexture(modelTextureNumber, textureInfo.id);

          this._textureCount++;

          if (this._textureCount >= textureCount) {
            // ロード完了
            this._state = LoadStep.CompleteSetup;
            this.textureMs = performance.now() - texturesStarted;
          }
        };

        // 読み込み
        this._subdelegate
          .getTextureManager()
          .createTextureFromPngFile(texturePath, usePremultiply, onLoad);
        this.getRenderer().setIsPremultipliedAlpha(usePremultiply);
      }

      this._state = LoadStep.WaitLoadTexture;
    }
  }

  /**
   * レンダラを再構築する
   */
  public reloadRenderer(): void {
    this.deleteRenderer();
    this.createRenderer();
    this.setupTextures();
  }

  /**
   * 更新
   */
  public update(): void {
    if (this._state != LoadStep.CompleteSetup) return;

    // The portrait consumes final state; it never runs another motion, blink or physics clock.
    if (portraitMode) {
      const frame = incomingFrame;
      if (!frame || frame.model !== this._modelHomeDir) return;
      const pose = frame.poseName ? this.extraPoses.get(frame.poseName) : null;
      if (frame.poseName && !pose) {
        document.documentElement.style.opacity = '0';
        this.loadPortraitPose(frame.poseName);
        return;
      }
      const core = this._model.getModel();
      if (frame.parameters.length !== core.parameters.values.length || frame.parts.length !== core.parts.opacities.length) return;
      core.parameters.values.set(frame.parameters);
      this.blendPortraitExpression(frame.parameters);
      core.parts.opacities.set(frame.parts);
      this._userTimeSeconds = frame.time;
      this.extraPose = pose;
      if (pose) pose.pose = frame.pose;
      this.poseTransition = null;
      this.streetPoseOpacity = frame.opacity;
      this._subdelegate.getCanvas().style.opacity = String(frame.opacity);
      this._model.update();
      mirrorStats.applied = frame.sequence;
      mirrorStats.latencyMs = Math.max(0, Date.now() - frame.sentAt);
      document.documentElement.style.opacity = '1';
      return;
    }

    const deltaTimeSeconds: number = LAppPal.getDeltaTime();
    this._userTimeSeconds += deltaTimeSeconds;

    this._dragManager.update(deltaTimeSeconds);
    this._dragX = this._dragManager.getX();
    this._dragY = this._dragManager.getY();

    // モーションによるパラメータ更新の有無
    let motionUpdated = false;

    //--------------------------------------------------------------------------
    this._model.loadParameters(); // 前回セーブされた状態をロード
    if (this._motionManager.isFinished()) {
      // モーションの再生がない場合、待機モーションの中からランダムで再生する
      this.startRandomMotion(
        LAppDefine.MotionGroupIdle,
        LAppDefine.PriorityIdle
      );
    } else {
      motionUpdated = this._motionManager.updateMotion(
        this._model,
        deltaTimeSeconds
      ); // モーションを更新
    }
    this._model.saveParameters(); // 状態を保存
    //--------------------------------------------------------------------------

    // まばたき
    if (!motionUpdated || this.extraPose?.sharedIdle) {
      if (this._eyeBlink != null) {
        // メインモーションの更新がないとき
        this._eyeBlink.updateParameters(this._model, deltaTimeSeconds); // 目パチ
      }
    }

    if (this._expressionManager != null) {
      this._expressionManager.updateMotion(this._model, deltaTimeSeconds); // 表情でパラメータ更新（相対変化）
    }

    // ドラッグによる変化
    // ドラッグによる顔の向きの調整
    this._model.addParameterValueById(this._idParamAngleX, this._dragX * 30); // -30から30の値を加える
    this._model.addParameterValueById(this._idParamAngleY, this._dragY * 30);
    this._model.addParameterValueById(
      this._idParamAngleZ,
      this._dragX * this._dragY * -30
    );

    // ドラッグによる体の向きの調整
    this._model.addParameterValueById(
      this._idParamBodyAngleX,
      this._dragX * 10
    ); // -10から10の値を加える

    // ドラッグによる目の向きの調整
    this._model.addParameterValueById(this._idParamEyeBallX, this._dragX); // -1から1の値を加える
    this._model.addParameterValueById(this._idParamEyeBallY, this._dragY);

    // 呼吸など
    if (this._breath != null) {
      const keep = this.extraPose?.sharedIdle ? [this._idParamAngleX,this._idParamAngleY,this._idParamAngleZ] : [];
      const angles = keep.map(id=>this._model.getParameterValueById(id));
      this._breath.updateParameters(this._model, deltaTimeSeconds);
      keep.forEach((id,index)=>{
        const amount=motionUpdated ? 0 : .10;
        const value=this._model.getParameterValueById(id);
        this._model.setParameterValueById(id,angles[index]+(value-angles[index])*amount);
      });
    }

    // 物理演算の設定
    if (this._physics != null) {
      this._physics.evaluate(this._model, deltaTimeSeconds);
    }

    // リップシンクの設定
    if (this._lipsync) {
      let value = 0.0; // リアルタイムでリップシンクを行う場合、システムから音量を取得して、0~1の範囲で値を入力します。

      this._wavFileHandler.update(deltaTimeSeconds);
      value = this._wavFileHandler.getRms();

      for (let i = 0; i < this._lipSyncIds.getSize(); ++i) {
        this._model.addParameterValueById(this._lipSyncIds.at(i), value, 0.8);
      }
    }

    // ポーズの設定
    if (this._pose != null) {
      this._pose.updateParameters(this._model, deltaTimeSeconds);
    }

    // Personal seated model: small procedural idle sway, not physics simulation.
    if (this._modelHomeDir.includes('/street_knees/') || this.usesStreetIdle()) {
      const manualSway = (window as any).petSwayOverride;
      this._model.setParameterValueById(
        CubismFramework.getIdManager().getId('ParamBodyAngleZ'),
        typeof manualSway === 'number' ? manualSway : 6 * Math.sin(this._userTimeSeconds * 0.75)
      );
      this._model.setParameterValueById(
        CubismFramework.getIdManager().getId('ParamHairFront'),
        0.55 * Math.sin(this._userTimeSeconds * 1.7)
      );
    }
    const desktopParameters = (window as any).petParameters;
    if (desktopParameters && performance.now() < desktopParameters.until) {
      for(const id of Object.keys(desktopParameters.values)) {
        this._model.setParameterValueById(CubismFramework.getIdManager().getId(id),Number(desktopParameters.values[id]));
      }
    }
    this.updateStreetPose(deltaTimeSeconds);
    this.blendDesktopParameters(deltaTimeSeconds);
    // A playing TTS mouth must survive procedural action face overrides.
    const speech = (window as any).petSpeech;
    if (speech && performance.now() < speech.until) {
      this._model.setParameterValueById(CubismFramework.getIdManager().getId('ParamMouthOpenY'), speech.mouth);
    }
    this._model.update();
  }

  /**
   * 引数で指定したモーションの再生を開始する
   * @param group モーショングループ名
   * @param no グループ内の番号
   * @param priority 優先度
   * @param onFinishedMotionHandler モーション再生終了時に呼び出されるコールバック関数
   * @return 開始したモーションの識別番号を返す。個別のモーションが終了したか否かを判定するisFinished()の引数で使用する。開始できない時は[-1]
   */
  public startMotion(
    group: string,
    no: number,
    priority: number,
    onFinishedMotionHandler?: FinishedMotionCallback,
    onBeganMotionHandler?: BeganMotionCallback,
    fadeInSeconds?: number
  ): CubismMotionQueueEntryHandle {
    if (priority == LAppDefine.PriorityForce) {
      this._motionManager.setReservePriority(priority);
    } else if (!this._motionManager.reserveMotion(priority)) {
      if (this._debugMode) {
        LAppPal.printMessage("[APP]can't start motion.");
      }
      return InvalidMotionQueueEntryHandleValue;
    }

    const motionFileName = this._modelSetting.getMotionFileName(group, no);

    // ex) idle_0
    const name = `${group}_${no}`;
    let motion: CubismMotion = this._motions.getValue(name) as CubismMotion;
    let autoDelete = false;

    if (motion == null) {
      fetch(`${this._modelHomeDir}${motionFileName}`)
        .then(response => {
          if (response.ok) {
            return response.arrayBuffer();
          } else if (response.status >= 400) {
            CubismLogError(
              `Failed to load file ${this._modelHomeDir}${motionFileName}`
            );
            return new ArrayBuffer(0);
          }
        })
        .then(arrayBuffer => {
          motion = this.loadMotion(
            arrayBuffer,
            arrayBuffer.byteLength,
            null,
            onFinishedMotionHandler,
            onBeganMotionHandler,
            this._modelSetting,
            group,
            no
          );

          if (motion == null) {
            return;
          }

          motion.setEffectIds(this._eyeBlinkIds, this._lipSyncIds);
          autoDelete = true; // 終了時にメモリから削除
        });
    } else {
      if (fadeInSeconds !== undefined) motion.setFadeInTime(fadeInSeconds);
      motion.setBeganMotionHandler(onBeganMotionHandler);
      motion.setFinishedMotionHandler(onFinishedMotionHandler);
    }

    //voice
    const voice = this._modelSetting.getMotionSoundFileName(group, no);
    if (voice.localeCompare('') != 0) {
      let path = voice;
      path = this._modelHomeDir + path;
      this._wavFileHandler.start(path);
    }

    if (this._debugMode) {
      LAppPal.printMessage(`[APP]start motion: [${group}_${no}`);
    }
    return this._motionManager.startMotionPriority(
      motion,
      autoDelete,
      priority
    );
  }

  /**
   * ランダムに選ばれたモーションの再生を開始する。
   * @param group モーショングループ名
   * @param priority 優先度
   * @param onFinishedMotionHandler モーション再生終了時に呼び出されるコールバック関数
   * @return 開始したモーションの識別番号を返す。個別のモーションが終了したか否かを判定するisFinished()の引数で使用する。開始できない時は[-1]
   */
  public startRandomMotion(
    group: string,
    priority: number,
    onFinishedMotionHandler?: FinishedMotionCallback,
    onBeganMotionHandler?: BeganMotionCallback
  ): CubismMotionQueueEntryHandle {
    if (this._modelSetting.getMotionCount(group) == 0) {
      return InvalidMotionQueueEntryHandleValue;
    }

    const no: number = Math.floor(
      Math.random() * this._modelSetting.getMotionCount(group)
    );

    return this.startMotion(
      group,
      no,
      priority,
      onFinishedMotionHandler,
      onBeganMotionHandler
    );
  }

  /**
   * 引数で指定した表情モーションをセットする
   *
   * @param expressionId 表情モーションのID
   */
  private blendPortraitExpression(base:number[]): void {
    const {name, previous, changedAt} = portraitExpression;
    const mix = Math.min(1, (performance.now() - changedAt) / 240);
    const layers = [[previous, 1 - mix], [name, mix]] as const;
    for (const [expression, weight] of layers) {
      if (!expression || weight <= 0) continue;
      const motion = this._expressions.getValue(expression);
      if (!(motion instanceof CubismExpressionMotion)) continue;
      const parameters = motion.getExpressionParameters();
      for (let i = 0; i < parameters.getSize(); i++) {
        const {parameterId, value, blendType} = parameters.at(i);
        if (parameterId.getString().s === 'ParamMouthOpenY') continue;
        const index = this._model.getParameterIndex(parameterId);
        const original = base[index];
        if (!Number.isFinite(original)) continue;
        const delta = blendType === ExpressionBlendType.Additive ? value :
          blendType === ExpressionBlendType.Multiply ? original * (value - 1) : value - original;
        this._model.addParameterValueByIndex(index, delta, weight);
      }
    }
  }

  public setExpression(expressionId: string): void {
    const motion: ACubismMotion = this._expressions.getValue(expressionId);

    if (this._debugMode) {
      LAppPal.printMessage(`[APP]expression: [${expressionId}]`);
    }

    if (motion != null) {
      this._expressionManager.startMotion(motion, false);
    } else {
      if (this._debugMode) {
        LAppPal.printMessage(`[APP]expression[${expressionId}] is null`);
      }
    }
  }

  /**
   * ランダムに選ばれた表情モーションをセットする
   */
  public setRandomExpression(): void {
    if (this._expressions.getSize() == 0) {
      return;
    }

    const no: number = Math.floor(Math.random() * this._expressions.getSize());

    for (let i = 0; i < this._expressions.getSize(); i++) {
      if (i == no) {
        const name: string = this._expressions._keyValues[i].first;
        this.setExpression(name);
        return;
      }
    }
  }

  /**
   * イベントの発火を受け取る
   */
  public motionEventFired(eventValue: csmString): void {
    CubismLogInfo('{0} is fired on LAppModel!!', eventValue.s);
  }

  /**
   * 当たり判定テスト
   * 指定ＩＤの頂点リストから矩形を計算し、座標をが矩形範囲内か判定する。
   *
   * @param hitArenaName  当たり判定をテストする対象のID
   * @param x             判定を行うX座標
   * @param y             判定を行うY座標
   */
  public hitTest(hitArenaName: string, x: number, y: number): boolean {
    // 透明時は当たり判定無し。
    if (this._opacity < 1) {
      return false;
    }

    const count: number = this._modelSetting.getHitAreasCount();

    for (let i = 0; i < count; i++) {
      if (this._modelSetting.getHitAreaName(i) == hitArenaName) {
        const drawId: CubismIdHandle = this._modelSetting.getHitAreaId(i);
        return this.isHit(drawId, x, y);
      }
    }

    return false;
  }

  /**
   * モーションデータをグループ名から一括でロードする。
   * モーションデータの名前は内部でModelSettingから取得する。
   *
   * @param group モーションデータのグループ名
   */
  public preLoadMotionGroup(group: string): void {
    for (let i = 0; i < this._modelSetting.getMotionCount(group); i++) {
      const motionFileName = this._modelSetting.getMotionFileName(group, i);

      // ex) idle_0
      const name = `${group}_${i}`;
      if (this._debugMode) {
        LAppPal.printMessage(
          `[APP]load motion: ${motionFileName} => [${name}]`
        );
      }

      fetch(`${this._modelHomeDir}${motionFileName}`)
        .then(response => {
          if (response.ok) {
            return response.arrayBuffer();
          } else if (response.status >= 400) {
            CubismLogError(
              `Failed to load file ${this._modelHomeDir}${motionFileName}`
            );
            return new ArrayBuffer(0);
          }
        })
        .then(arrayBuffer => {
          const tmpMotion: CubismMotion = this.loadMotion(
            arrayBuffer,
            arrayBuffer.byteLength,
            name,
            null,
            null,
            this._modelSetting,
            group,
            i
          );

          if (tmpMotion != null) {
            tmpMotion.setEffectIds(this._eyeBlinkIds, this._lipSyncIds);

            if (this._motions.getValue(name) != null) {
              ACubismMotion.delete(this._motions.getValue(name));
            }

            this._motions.setValue(name, tmpMotion);

            this._motionCount++;
            if (this._motionCount >= this._allMotionCount) {
              this._state = LoadStep.LoadTexture;

              // 全てのモーションを停止する
              this._motionManager.stopAllMotions();

              this._updating = false;
              this._initialized = true;

              this.createRenderer();
              this.setupTextures();
              this.getRenderer().startUp(
                this._subdelegate.getGlManager().getGl()
              );
            }
          } else {
            // loadMotionできなかった場合はモーションの総数がずれるので1つ減らす
            this._allMotionCount--;
          }
        });
    }
  }

  /**
   * すべてのモーションデータを解放する。
   */
  public releaseMotions(): void {
    this._motions.clear();
  }

  /**
   * 全ての表情データを解放する。
   */
  public releaseExpressions(): void {
    this._expressions.clear();
  }

  /**
   * モデルを描画する処理。モデルを描画する空間のView-Projection行列を渡す。
   */
  faceRig: FaceRig | null = null;
  faceLoading = false;
  faceError = '';
  private faceReleased = false;
  private streetIdle: StreetIdle | null = null;
  private extraPoses = new Map<string,StreetPose>();
  private desktopPoses: PoseCache | null = null;
  private pendingDesktopPose: {pose: StreetPose | null; autoLead?: number} | null = null;
  public get autonomousPoseDone() { return this.extraPose?.autonomousDone ?? false; }
  private desktopBlend: {values: Float32Array; elapsed: number; duration: number} | null = null;
  private lastDesktopParameters: Float32Array | null = null;
  private desktopBlendIndices: number[] | null = null;
  public get fastDesktopSwitch(): boolean {
    return !portraitMode && desktopMode && this.usesStreetIdle();
  }
  public get desktopCacheStats() { return this.desktopPoses?.stats; }

  public prepareDesktopPose(name: string): Promise<StreetPose> {
    if (!this.desktopPoses || !poseFolders[name]) return Promise.reject(Error('未知动作：' + name));
    return this.desktopPoses.request(name);
  }

  public warmDesktopOutfit(name: string | null): void {
    this.desktopPoses?.warm(name);
  }

  public dropWaitingDesktopPoses(): void {
    this.desktopPoses?.cancelWaiting();
  }

  public beginDesktopBlend(duration: number): void {
    if (!this.fastDesktopSwitch || !this.lastDesktopParameters) return;
    if (duration <= 0) { this.desktopBlend=null; return; }
    this.desktopBlend = {values: this.lastDesktopParameters.slice(), elapsed: 0, duration};
  }

  private blendDesktopParameters(dt: number): void {
    if (!this.fastDesktopSwitch) return;
    const params = this._model.getModel().parameters;
    const values = params.values;
    if (!this.lastDesktopParameters) this.lastDesktopParameters = new Float32Array(values.length);
    const blend = this.desktopBlend;
    if (blend) {
      // Let eyelids, lip sync, breathing and hair physics keep running normally.
      this.desktopBlendIndices ??= params.ids.flatMap((id, i) =>
        /^(ParamAngle[XYZ]|ParamEyeBall[XY]|ParamBodyAngle[XYZ])$/.test(id) ? [i] : []);
      blend.elapsed += Math.max(0, Math.min(.12, dt));
      const t = Math.min(1, blend.elapsed / blend.duration);
      const progress = t*t*(3-2*t);
      for (const i of this.desktopBlendIndices) values[i] = blend.values[i] + (values[i]-blend.values[i])*progress;
      if (t === 1) this.desktopBlend = null;
    }
    this.lastDesktopParameters.set(values);
  }
  private poseErrors: Record<string,string> = {};
  private posesStarted = false;
  private modelStarted = 0;
  public modelMs = 0;
  public textureMs = 0;
  private extraPose: StreetPose | null = null;
  private poseTransition: {elapsed: number; target: StreetPose | null; switched: boolean} | null = null;
  public streetPoseOpacity = 1;
  public get streetPoseChanging() { return !!(this.poseTransition || this.pendingDesktopPose || this.desktopBlend); }
  public get streetPose() { return this.extraPose?.pose ?? null; }
  public get streetPoseName() { return this.extraPose?.name ?? null; }
  public get poseReadiness() { return [...this.extraPoses.keys()]; }
  public get streetPoseErrors() { return this.poseErrors; }

  private portraitLoading = new Set<string>();
  private loadPortraitPose(name:string): void {
    if (!poseFolders[name] || this.portraitLoading.has(name) || this.faceReleased) return;
    this.portraitLoading.add(name);
    StreetPose.load(name,this._modelHomeDir+'actions/'+poseFolders[name]+'/',this._model,this.getRenderer())
      .then(pose=>{ if(this.faceReleased) pose.dispose(); else this.extraPoses.set(name,pose); })
      .catch(error=>{this.poseErrors[name]=String(error);});
  }

  public stopDesktopMotion(): void {
    this._motionManager.stopAllMotions();
    this._motionManager.setReservePriority(0);
  }

  public idleOutfit(): void {
    this.stopDesktopMotion();
    this._model.loadParameters();
    for (const key of ['ParamAngleX','ParamAngleZ','ParamEyeBallX','ParamEyeBallY']) {
      this._model.setParameterValueById(CubismFramework.getIdManager().getId(key),0);
    }
    this._model.saveParameters();
  }
  public setStreetPose(name: string | null, duration = transitionTime.action, autoLead?: number): boolean {
    const pose = name ? this.extraPoses.get(name) : null;
    if (pose === undefined) return false;
    if (this.fastDesktopSwitch) {
      if (this.pendingDesktopPose?.pose === pose || (!this.pendingDesktopPose && this.extraPose === pose)) return true;
      this.pendingDesktopPose = {pose, autoLead};
      this.beginDesktopBlend(duration);
      this.desktopPoses?.protect(this.extraPose?.name, pose?.name);
    } else this.transitionStreetPose(pose);
    return true;
  }

  private transitionStreetPose(target: StreetPose | null): void {
    if (this.poseTransition?.target === target) return;
    if (!this.poseTransition && this.extraPose === target) return;
    const elapsed = this.poseTransition?.elapsed ?? 0;
    this.poseTransition = {elapsed: elapsed > .16 ? .32-elapsed : elapsed, target, switched: false};
  }

  private updateStreetPose(dt: number): void {
    const step = Math.max(0, Math.min(.12, dt));
    if (this.pendingDesktopPose) {
      const {pose, autoLead} = this.pendingDesktopPose;
      this.pendingDesktopPose = null;
      this.extraPose = pose;
      this.extraPose?.start(autoLead);
      this.desktopPoses?.protect(pose?.name);
    }
    const transition = this.poseTransition;
    if (transition) {
      transition.elapsed += step;
      if (!transition.switched && transition.elapsed >= .16) {
        transition.elapsed = .16;
        transition.switched = true;
        this.extraPose = transition.target;
        if (this.extraPose) {
          this._motionManager.stopAllMotions();
          this.extraPose.start();
        }
      }
      const opacity = Math.min(1, Math.abs(transition.elapsed-.16)/.16);
      this.streetPoseOpacity = opacity*opacity*(3-2*opacity);
      this._subdelegate.getCanvas().style.opacity = String(this.streetPoseOpacity);
      if (transition.elapsed >= .32) this.poseTransition = null;
    }
    this.extraPose?.apply(step);
  }
  public release(): void {
    this.faceReleased = true;
    this.faceRig?.dispose();
    this.faceRig = null;
    this.desktopPoses?.dispose();
    this.extraPoses.forEach(pose=>pose.dispose());
    this.extraPoses.clear();
    this.extraPose=null;
    if (this.poseTransition) this._subdelegate.getCanvas().style.opacity = '';
    super.release();
  }

  public usesStreetIdle(): boolean {
    return this._modelHomeDir?.endsWith('/street_neck_rebuild_v20/') ?? false;
  }

  public doDraw(): void {
    if (this._model == null) return;

    // キャンバスサイズを渡す
    const canvas = this._subdelegate.getCanvas();
    const viewport: number[] = [0, 0, canvas.width, canvas.height];

    this.getRenderer().setRenderState(
      this._subdelegate.getFrameBuffer(),
      viewport
    );
    const renderer = this.getRenderer();
    if (this.usesStreetIdle() && !this.posesStarted) {
      this.posesStarted=true;
      const outfit = new URLSearchParams(location.search).get('outfit');
      const preview = new URLSearchParams(location.search).has('outfitPreview');
      const names = preview ? Object.keys(poseFolders) : outfitPoses(outfit);
      if (this.fastDesktopSwitch && !preview) {
        this.desktopPoses = new PoseCache(this.extraPoses, this.poseErrors,
          name => StreetPose.load(name,this._modelHomeDir+'actions/'+poseFolders[name]+'/',this._model,renderer));
        this.desktopPoses.warm(outfit);
      }
      for (const name of this.desktopPoses ? [] : names) {
        if (portraitMode) { this.loadPortraitPose(name); continue; }
        StreetPose.load(name,this._modelHomeDir+'actions/'+poseFolders[name]+'/',this._model,renderer).then(pose=>{
          if(this.faceReleased) pose.dispose();
          else this.extraPoses.set(name,pose);
        }).catch(error=>{ this.poseErrors[name]=String(error); });
      }
    }
    if (this.extraPose?.pose) {
      if(this.extraPose.sharedIdle) {
        this.streetIdle ??= new StreetIdle(this._model);
        this.streetIdle.draw(() => this.extraPose.draw());
      } else this.extraPose.draw();
      return;
    }
    if (this._modelHomeDir.endsWith('/street_knees/')) {
      if (!this.faceLoading) {
        this.faceLoading = true;
        FaceRig.load(this._modelHomeDir, this._model, renderer).then(rig => {
          if (this.faceReleased) rig.dispose();
          else this.faceRig = rig;
        }).catch(error => { this.faceError = String(error); });
      }
      if (this.faceRig) {
        this.faceRig.draw(renderer, this._userTimeSeconds);
        return;
      }
    }
    if (this.usesStreetIdle()) {
      this.streetIdle ??= new StreetIdle(this._model);
      this.streetIdle.draw(() => renderer.drawModel());
    } else {
      renderer.drawModel();
    }
  }

  /**
   * モデルを描画する処理。モデルを描画する空間のView-Projection行列を渡す。
   */
  public draw(matrix: CubismMatrix44): void {
    if (this._model == null) {
      return;
    }

    // 各読み込み終了後
    if (this._state == LoadStep.CompleteSetup) {
      matrix.multiplyByMatrix(this._modelMatrix);

      this.getRenderer().setMvpMatrix(matrix);

      this.doDraw();
    }
  }

  public async hasMocConsistencyFromFile() {
    CSM_ASSERT(this._modelSetting.getModelFileName().localeCompare(``));

    // CubismModel
    if (this._modelSetting.getModelFileName() != '') {
      const modelFileName = this._modelSetting.getModelFileName();

      const response = await fetch(`${this._modelHomeDir}${modelFileName}`);
      const arrayBuffer = await response.arrayBuffer();

      this._consistency = CubismMoc.hasMocConsistency(arrayBuffer);

      if (!this._consistency) {
        CubismLogInfo('Inconsistent MOC3.');
      } else {
        CubismLogInfo('Consistent MOC3.');
      }

      return this._consistency;
    } else {
      LAppPal.printMessage('Model data does not exist.');
    }
  }

  public setSubdelegate(subdelegate: LAppSubdelegate): void {
    this._subdelegate = subdelegate;
  }

  /**
   * コンストラクタ
   */
  public constructor() {
    super();

    this._modelSetting = null;
    this._modelHomeDir = null;
    this._userTimeSeconds = 0.0;

    this._eyeBlinkIds = new csmVector<CubismIdHandle>();
    this._lipSyncIds = new csmVector<CubismIdHandle>();

    this._motions = new csmMap<string, ACubismMotion>();
    this._expressions = new csmMap<string, ACubismMotion>();

    this._hitArea = new csmVector<csmRect>();
    this._userArea = new csmVector<csmRect>();

    this._idParamAngleX = CubismFramework.getIdManager().getId(
      CubismDefaultParameterId.ParamAngleX
    );
    this._idParamAngleY = CubismFramework.getIdManager().getId(
      CubismDefaultParameterId.ParamAngleY
    );
    this._idParamAngleZ = CubismFramework.getIdManager().getId(
      CubismDefaultParameterId.ParamAngleZ
    );
    this._idParamEyeBallX = CubismFramework.getIdManager().getId(
      CubismDefaultParameterId.ParamEyeBallX
    );
    this._idParamEyeBallY = CubismFramework.getIdManager().getId(
      CubismDefaultParameterId.ParamEyeBallY
    );
    this._idParamBodyAngleX = CubismFramework.getIdManager().getId(
      CubismDefaultParameterId.ParamBodyAngleX
    );

    if (LAppDefine.MOCConsistencyValidationEnable) {
      this._mocConsistency = true;
    }

    this._state = LoadStep.LoadAssets;
    this._expressionCount = 0;
    this._textureCount = 0;
    this._motionCount = 0;
    this._allMotionCount = 0;
    this._wavFileHandler = new LAppWavFileHandler();
    this._consistency = false;
  }

  private _subdelegate: LAppSubdelegate;

  _modelSetting: ICubismModelSetting; // モデルセッティング情報
  _modelHomeDir: string; // モデルセッティングが置かれたディレクトリ
  _userTimeSeconds: number; // デルタ時間の積算値[秒]

  _eyeBlinkIds: csmVector<CubismIdHandle>; // モデルに設定された瞬き機能用パラメータID
  _lipSyncIds: csmVector<CubismIdHandle>; // モデルに設定されたリップシンク機能用パラメータID

  _motions: csmMap<string, ACubismMotion>; // 読み込まれているモーションのリスト
  _expressions: csmMap<string, ACubismMotion>; // 読み込まれている表情のリスト

  _hitArea: csmVector<csmRect>;
  _userArea: csmVector<csmRect>;

  _idParamAngleX: CubismIdHandle; // パラメータID: ParamAngleX
  _idParamAngleY: CubismIdHandle; // パラメータID: ParamAngleY
  _idParamAngleZ: CubismIdHandle; // パラメータID: ParamAngleZ
  _idParamEyeBallX: CubismIdHandle; // パラメータID: ParamEyeBallX
  _idParamEyeBallY: CubismIdHandle; // パラメータID: ParamEyeBAllY
  _idParamBodyAngleX: CubismIdHandle; // パラメータID: ParamBodyAngleX

  _state: LoadStep; // 現在のステータス管理用
  _expressionCount: number; // 表情データカウント
  _textureCount: number; // テクスチャカウント
  _motionCount: number; // モーションデータカウント
  _allMotionCount: number; // モーション総数
  _wavFileHandler: LAppWavFileHandler; //wavファイルハンドラ
  _consistency: boolean; // MOC3一貫性チェック管理用
}

