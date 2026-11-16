const $ = id => document.getElementById(id);
let music, authorized = false, accessVerified = false, busy = false, token = '', authRevision = 0;
let configurationError = '', commandResult = null, requestedId = 0, selected = false;
let sdkReady = false, sdkLoadError = '';
window.ttSetActive = active => { selected = !!active; };

function say(text) { $('status').textContent = text; }
function waitForMusicKit() {
    if (window.MusicKit) return Promise.resolve();
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            document.removeEventListener('musickitloaded', loaded);
            reject(Error('SDK'));
        }, 10000);
        function loaded() {
            clearTimeout(timer);
            resolve();
        }
        document.addEventListener('musickitloaded', loaded, {once:true});
    });
}
function controls() {
    $('authorize').disabled = !music || authorized || busy;
    $('disconnect').disabled = !music || !authorized || busy;
    $('find').disabled = !music || busy;
    $('library').disabled = !accessVerified || busy;
}
waitForMusicKit().then(() => { sdkReady = true; }).catch(() => {
    sdkLoadError = '无法加载 Apple MusicKit JS，请检查网络或 HTTPS。';
    if (!music) say(sdkLoadError);
});
function errorText(error, fallback) {
    const code = error?.status || error?.statusCode || error?.code || error?.name;
    if (code === 401 || code === '401') return 'Apple 拒绝了开发者令牌，请检查签名、有效期和来源。';
    if (code === 403 || code === '403' || code === 'AUTHORIZATION_ERROR') return 'Apple 拒绝授权。请检查账户、订阅和地区后重新授权。';
    if (code === 429 || code === '429') return 'Apple 请求过多，请稍后重试。';
    return fallback;
}
async function verifyAccount(candidate) {
    if (!music.isAuthorized) return false;
    await music.api.music('/v1/me/storefront');
    authorized = true;
    accessVerified = true;
    token = typeof candidate === 'string' ? candidate : (music.musicUserToken || '');
    authRevision++;
    return true;
}
window.ttConfigure = async (developerToken, savedUserToken) => {
    try {
        await waitForMusicKit();
        sdkReady = true;
        await MusicKit.configure({developerToken, app:{name:'TT 桌宠',build:'1.0'}});
        music = MusicKit.getInstance();
        if (savedUserToken && !music.isAuthorized) {
            // SDK versions may expose the public instance property as read-only.
            try { music.musicUserToken = savedUserToken; } catch (_) { /* authorize stays available */ }
        }
        if (music.isAuthorized && await verifyAccount(savedUserToken || null)) {
            say('Apple 账户已授权。请选择歌曲。');
        } else {
            say(savedUserToken ? '请在 Apple 页面恢复本机的授权。' : '请使用 Apple 官方页面授权。');
        }
    } catch (error) {
        configurationError = errorText(error, 'MusicKit 初始化或验证失败。请检查开发者配置与网络。');
        say(configurationError);
    } finally { controls(); }
};

async function interactive(action, fallback, requiresAccount=false) {
    if (busy) return;
    busy = true;
    controls();
    try { await action(); }
    catch (error) {
        const code = error?.status || error?.statusCode || error?.code;
        if (requiresAccount && [403, '403', 'AUTHORIZATION_ERROR'].includes(code)) {
            accessVerified = false;
            authorized = false;
        }
        say(errorText(error, fallback));
    } finally { busy = false; controls(); }
}
$('authorize').onclick = () => interactive(async () => {
    const result = await music.authorize();
    say(await verifyAccount(result) ? 'Apple 账户已授权。请选择歌曲。' : '尚未完成 Apple 授权。');
}, 'Apple 授权没有完成。请检查弹窗、账户和订阅后重试。', true);

async function disconnect() {
    if (music.isPlaying) await music.pause();
    await music.unauthorize();
    token = '';
    authorized = false;
    accessVerified = false;
    authRevision++;
    $('results').replaceChildren();
    say('已断开 Apple 授权。');
    controls();
}
$('disconnect').onclick = () => interactive(disconnect, 'Apple 未确认断开，请重试。', true);

function artwork(item) {
    const url = item?.attributes?.artwork?.url;
    if (typeof url !== 'string') return '';
    try {
        const value = new URL(url.replaceAll('{w}', '160').replaceAll('{h}', '160'));
        return value.protocol==='https:' && value.hostname.endsWith('.mzstatic.com') ? value.href : '';
    } catch (_) { return ''; }
}

function render(items) {
    const list = $('results');
    list.replaceChildren();
    for (const item of items) {
        const li = document.createElement('li');
        const image = artwork(item);
        if (image) {
            const cover = document.createElement('img');
            cover.alt = '';
            cover.src = image;
            li.append(cover);
        }
        const button = document.createElement('button');
        button.textContent = [item.attributes?.name, item.attributes?.artistName].filter(Boolean).join(' · ') || 'Apple Music';
        button.onclick = () => interactive(async () => {
            if (item.type === 'artists') {
                const result = await music.api.music(`/v1/catalog/${music.storefrontId}/artists/${encodeURIComponent(item.id)}/albums`);
                render(result.data.data || []);
                return;
            }
            if (item.type === 'library-playlists') {
                const result = await music.api.music(`/v1/me/library/playlists/${encodeURIComponent(item.id)}/tracks`);
                render(result.data.data || []);
                return;
            }
            if (!accessVerified) { say('请先完成 Apple 授权，再播放曲目。'); return; }
            if (music.hasMusicSubscription === false) { say('Apple Music 未提供有效的播放订阅。'); return; }
            if (!selected) { say('请在 TT 的音乐平台设置中将 MusicKit 设为当前来源。'); return; }
            const key = item.type === 'songs' ? 'song' : item.type === 'albums' ? 'album' : '';
            if (key) await music.setQueue({[key]:item.id});
            else if (item.type === 'library-songs' && item.attributes?.playParams?.catalogId) {
                await music.setQueue({song:item.attributes.playParams.catalogId});
            } else { say('此曲目没有可用的 Apple Music 曲库播放标识。'); return; }
            await music.play();
            say('已向 MusicKit 请求播放，实际状态以播放器为准。');
        }, '无法播放或加载。请检查订阅、地区、网络及 WebEngine 的受保护媒体支持。', true);
        li.append(button);
        list.append(li);
    }
    if (!items.length) say('没有找到可显示的项目。');
}

$('search').onsubmit = event => {
    event.preventDefault();
    interactive(async () => {
        const kind = $('kind').value;
        if (!music.storefrontId) { say('未能确定 Apple Music 店面，请授权后重试。'); return; }
        const result = await music.api.music(`/v1/catalog/${music.storefrontId}/search`,
            {term:$('query').value.trim(),types:kind,limit:20});
        render(result.data.results?.[kind]?.data || []);
    }, '搜索失败，请检查开发者令牌和网络。');
};
$('library').onclick = () => interactive(async () => {
    const result = await music.api.music('/v1/me/library/playlists',{limit:100});
    render(result.data.data || []);
}, '无法读取歌单，请检查 Apple 授权与订阅。', true);

window.ttMusicState = revision => {
    const item = music?.nowPlayingItem;
    const duration = Number(music?.currentPlaybackDuration) || 0;
    const volume = music?.volume;
    const capabilities = {};
    if (accessVerified && music.hasMusicSubscription !== false && item) {
        capabilities.play = !music.isPlaying;
        capabilities.pause = !!music.isPlaying;
        capabilities.next = !!music.queue?.nextPlayableItem;
        capabilities.previous = !!music.queue?.previousPlayableItem;
        capabilities.seek = duration > 0 && typeof music.seekToTime === 'function';
        capabilities.volume = Number.isFinite(volume) && volume >= 0 && volume <= 1;
    }
    return { configured:!!music, sdkReady, authorized:accessVerified, error:configurationError || sdkLoadError,
        subscription:typeof music?.hasMusicSubscription === 'boolean' ? music.hasMusicSubscription : null,
        revision:authRevision, userToken:authRevision!==revision ? token : undefined, answer:commandResult,
        state:{title:item?.attributes?.name || item?.title || '',artist:item?.attributes?.artistName || item?.artistName || '',
            album:item?.attributes?.albumName || '', artUrl:artwork(item),
            status:music?.isPlaying ? 'Playing' : item ? 'Paused' : 'Stopped',
            duration:Math.round(duration*1000),position:Math.round((Number(music?.currentPlaybackTime)||0)*1000),
            volume:Number.isFinite(volume) && volume >= 0 && volume <= 1 ? Math.round(volume*100) : null,capabilities} };
};

window.ttMusicCommand = async (id, action, value) => {
    if (!music || requestedId===id) return;
    requestedId = id;
    try {
        if (action === 'test') {
            if (!await verifyAccount(null)) throw {code:'AUTHORIZATION_ERROR'};
        }
        else if (action === 'disconnect') await disconnect();
        else if (action === 'play') await music.play();
        else if (action === 'pause') await music.pause();
        else if (action === 'next') await music.skipToNextItem();
        else if (action === 'previous') await music.skipToPreviousItem();
        else if (action === 'seek') {
            const duration = Number(music.currentPlaybackDuration);
            if (!Number.isFinite(value) || !Number.isFinite(duration) || duration <= 0 ||
                value < 0 || value > Math.round(duration*1000)) {
                commandResult = {id,ok:false,error:'目标进度超出 MusicKit 当前曲目的时长。'};
                return;
            }
            await music.seekToTime(Math.min(value/1000,duration));
        }
        else if (action === 'volume') {
            if (!Number.isFinite(value) || value < 0 || value > 100 || !Number.isFinite(music.volume)) {
                commandResult = {id,ok:false,error:'MusicKit 播放器未提供有效的音量控制。'};
                return;
            }
            music.volume = value/100;
            if (Math.round(music.volume*100)!==Math.round(value)) {
                commandResult = {id,ok:false,error:'MusicKit 未确认新的播放器音量。'};
                return;
            }
        }
        else throw Error('Unknown command');
        commandResult = {id, ok:true};
    } catch (error) {
        const code = error?.status || error?.statusCode || error?.code;
        if ([403, '403', 'AUTHORIZATION_ERROR'].includes(code)) { accessVerified = false; authorized = false; controls(); }
        commandResult = {id, ok:false, error:errorText(error, 'MusicKit 未完成操作。请检查授权、网络及播放环境。')};
        say(commandResult.error);
    }
};
