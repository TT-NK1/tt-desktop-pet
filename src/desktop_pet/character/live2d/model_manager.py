import json
from pathlib import Path


class ModelError(ValueError):
    pass


def local_file(folder, relative):
    if not isinstance(relative, str) or not relative:
        raise ModelError("模型资源路径为空")
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ModelError("模型资源必须位于模型目录内")
    if not path.is_file():
        raise ModelError(f"缺少模型资源：{relative}")
    return path


def inspect_model(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ModelError(f"缺少模型描述文件：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        refs = data["FileReferences"]
        moc = local_file(path.parent, refs["Moc"])
        with moc.open("rb") as handle:
            if handle.read(4) != b"MOC3":
                raise ModelError("模型文件不是 MOC3")
        textures = refs["Textures"]
        if not isinstance(textures, list) or not textures:
            raise ModelError("缺少模型纹理")
        for texture in textures:
            local_file(path.parent, texture)
        for name in ("Physics", "Pose", "DisplayInfo"):
            if refs.get(name):
                local_file(path.parent, refs[name])
        for group in refs.get("Motions", {}).values():
            for motion in group:
                local_file(path.parent, motion["File"])
                if motion.get("Sound"):
                    local_file(path.parent, motion["Sound"])
        for expression in refs.get("Expressions", []):
            local_file(path.parent, expression["File"])
        return data
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ModelError(f"模型描述无法读取：{error}") from error


def audit(project):
    project = Path(project)
    groups = {
        "moc3": "*.moc3", "model3": "*.model3.json",
        "physics3": "*.physics3.json", "motion3": "*.motion3.json",
        "expressions": "*.exp3.json", "layered_psd": "*.psd",
        "layered_psb": "*.psb", "cubism_core": "live2dcubismcore*.js",
    }
    assets = project / "assets"
    result = {key: [str(p.relative_to(project)) for p in assets.rglob(pattern)]
              for key, pattern in groups.items()}
    vendor = project / "vendor"
    result["cubism_core"] += [str(p.relative_to(project))
                               for p in vendor.rglob("live2dcubismcore*.js")]
    return result
