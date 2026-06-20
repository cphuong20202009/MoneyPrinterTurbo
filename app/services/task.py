import math
import os.path
import re
import copy
import threading
import unicodedata
from os import path

from loguru import logger

from app.config import config
from app.models import const
from app.models.schema import VideoConcatMode, VideoParams, VideoTransitionMode
from app.services import buffer_post, llm, material, subtitle, video, voice, upload_post
from app.services import state as sm
from app.utils import utils

_VERSION_COUNT_DEFAULT = 5
_VERSION_COUNT_MAX = 5
_cancelled_tasks = set()
_cancel_lock = threading.Lock()


class TaskCancelled(Exception):
    pass


def request_cancel_task(task_id: str):
    with _cancel_lock:
        _cancelled_tasks.add(task_id)
    sm.state.update_task(task_id, state=const.TASK_STATE_CANCELED)
    logger.info(f"cancel requested for task: {task_id}")


def clear_cancel_task(task_id: str):
    with _cancel_lock:
        _cancelled_tasks.discard(task_id)


def is_task_cancelled(task_id: str) -> bool:
    with _cancel_lock:
        return task_id in _cancelled_tasks


def abort_if_cancelled(task_id: str):
    if is_task_cancelled(task_id):
        sm.state.update_task(task_id, state=const.TASK_STATE_CANCELED)
        logger.info(f"task canceled: {task_id}")
        raise TaskCancelled(task_id)


_VERSION_STYLES = [
    {
        "name": "fast hook",
        "concat_mode": VideoConcatMode.random,
        "transition_mode": VideoTransitionMode.none,
        "clip_delta": -1,
        "prompt": "Use a fast hook, punchy short sentences, and direct practical wording.",
    },
    {
        "name": "storytelling",
        "concat_mode": VideoConcatMode.sequential,
        "transition_mode": VideoTransitionMode.fade_in,
        "clip_delta": 0,
        "prompt": "Use a storytelling angle with a different opening scene and warmer emotional pacing.",
    },
    {
        "name": "educational",
        "concat_mode": VideoConcatMode.random,
        "transition_mode": VideoTransitionMode.slide_in,
        "clip_delta": 1,
        "prompt": "Use an educational explainer style with clear cause-and-effect structure.",
    },
    {
        "name": "dramatic",
        "concat_mode": VideoConcatMode.sequential,
        "transition_mode": VideoTransitionMode.fade_out,
        "clip_delta": 2,
        "prompt": "Use a more dramatic, curiosity-driven style with fresh examples and different wording.",
    },
    {
        "name": "social punchline",
        "concat_mode": VideoConcatMode.random,
        "transition_mode": VideoTransitionMode.shuffle,
        "clip_delta": -1,
        "prompt": "Use a social-video style with a strong final takeaway and no repeated phrasing from other versions.",
    },
]


def _safe_video_filename_stem(video_subject: str | None) -> str:
    subject = (video_subject or "").replace("Đ", "D").replace("đ", "d")
    normalized_subject = unicodedata.normalize("NFKD", subject)
    ascii_subject = normalized_subject.encode("ascii", "ignore").decode("ascii")
    safe_name = re.sub(r"[^A-Za-z0-9]+", "-", ascii_subject.lower()).strip("-")
    safe_name = re.sub(r"-{2,}", "-", safe_name)
    return safe_name[:80] or "video"


def _final_video_filename(video_subject: str | None, index: int) -> str:
    return f"{_safe_video_filename_stem(video_subject)}-{index}.mp4"


def generate_script(task_id, params):
    logger.info("\n\n## generating video script")
    video_script = params.video_script.strip()
    if not video_script:
        video_script = llm.generate_script(
            video_subject=params.video_subject,
            language=params.video_language,
            paragraph_number=params.paragraph_number,
            video_script_prompt=params.video_script_prompt,
            custom_system_prompt=params.custom_system_prompt,
        )
    else:
        logger.debug(f"video script: \n{video_script}")

    if not video_script:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error("failed to generate video script.")
        return None

    return video_script


def generate_terms(task_id, params, video_script):
    logger.info("\n\n## generating video terms")
    video_terms = params.video_terms
    if not video_terms:
        video_terms = llm.generate_terms(
            video_subject=params.video_subject, video_script=video_script, amount=5
        )
    else:
        if isinstance(video_terms, str):
            video_terms = [term.strip() for term in re.split(r"[,，]", video_terms)]
        elif isinstance(video_terms, list):
            video_terms = [term.strip() for term in video_terms]
        else:
            raise ValueError("video_terms must be a string or a list of strings.")

        logger.debug(f"video terms: {utils.to_json(video_terms)}")

    if not video_terms:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error("failed to generate video terms.")
        return None

    return video_terms


def save_script_data(task_id, video_script, video_terms, params):
    script_file = path.join(utils.task_dir(task_id), "script.json")
    script_data = {
        "script": video_script,
        "search_terms": video_terms,
        "params": params,
    }

    with open(script_file, "w", encoding="utf-8") as f:
        f.write(utils.to_json(script_data))


def generate_audio(task_id, params, video_script, version_index=None):
    '''
    Generate audio for the video script.
    If a custom audio file is provided, it will be used directly.
    There will be no subtitle maker object returned in this case.
    Otherwise, TTS will be used to generate the audio.
    Returns:
        - audio_file: path to the generated or provided audio file
        - audio_duration: duration of the audio in seconds
        - sub_maker: subtitle maker object if TTS is used, None otherwise
    '''
    logger.info("\n\n## generating audio")
    # /audio 和 /subtitle 请求模型不包含 custom_audio_file，
    # 这里统一做兼容读取，避免直调接口时抛属性错误。
    custom_audio_file = getattr(params, "custom_audio_file", None)
    if not custom_audio_file or not os.path.exists(custom_audio_file):
        if custom_audio_file:
            logger.warning(
                f"custom audio file not found: {custom_audio_file}, using TTS to generate audio."
            )
        else:
            logger.info("no custom audio file provided, using TTS to generate audio.")
        audio_filename = (
            f"audio-{version_index}.mp3" if version_index else "audio.mp3"
        )
        audio_file = path.join(utils.task_dir(task_id), audio_filename)
        sub_maker = voice.tts(
            text=video_script,
            voice_name=voice.parse_voice_name(params.voice_name),
            voice_rate=params.voice_rate,
            voice_file=audio_file,
        )
        if sub_maker is None:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error(
                """failed to generate audio:
1. check if the language of the voice matches the language of the video script.
2. check if the network is available. If you are in China, it is recommended to use a VPN and enable the global traffic mode.
            """.strip()
            )
            return None, None, None
        audio_duration = math.ceil(voice.get_audio_duration(sub_maker))
        if audio_duration == 0:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error("failed to get audio duration.")
            return None, None, None
        return audio_file, audio_duration, sub_maker
    else:
        logger.info(f"using custom audio file: {custom_audio_file}")
        audio_duration = voice.get_audio_duration(custom_audio_file)
        if audio_duration == 0:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error("failed to get audio duration from custom audio file.")
            return None, None, None
        return custom_audio_file, audio_duration, None

def generate_subtitle(task_id, params, video_script, sub_maker, audio_file, version_index=None):
    '''
    Generate subtitle for the video script.
    If subtitle generation is disabled or no subtitle maker is provided, it will return an empty string.
    Otherwise, it will generate the subtitle using the specified provider.
    Returns:
        - subtitle_path: path to the generated subtitle file
    '''
    logger.info("\n\n## generating subtitle")
    if not params.subtitle_enabled or sub_maker is None:
        return ""

    subtitle_filename = (
        f"subtitle-{version_index}.srt" if version_index else "subtitle.srt"
    )
    subtitle_path = path.join(utils.task_dir(task_id), subtitle_filename)
    subtitle_provider = config.app.get("subtitle_provider", "edge").strip().lower()
    logger.info(f"\n\n## generating subtitle, provider: {subtitle_provider}")

    subtitle_fallback = False
    if subtitle_provider == "edge":
        voice.create_subtitle(
            text=video_script, sub_maker=sub_maker, subtitle_file=subtitle_path
        )
        if not os.path.exists(subtitle_path):
            subtitle_fallback = True
            logger.warning("subtitle file not found, fallback to whisper")

    if subtitle_provider == "whisper" or subtitle_fallback:
        subtitle.create(audio_file=audio_file, subtitle_file=subtitle_path)
        logger.info("\n\n## correcting subtitle")
        subtitle.correct(subtitle_file=subtitle_path, video_script=video_script)

    subtitle_lines = subtitle.file_to_subtitles(subtitle_path)
    if not subtitle_lines:
        logger.warning(f"subtitle file is invalid: {subtitle_path}")
        return ""

    return subtitle_path


def get_video_materials(task_id, params, video_terms, audio_duration):
    if params.video_source == "local":
        logger.info("\n\n## preprocess local materials")
        materials = video.preprocess_video(
            materials=params.video_materials, clip_duration=params.video_clip_duration
        )
        if not materials:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error(
                "no valid materials found, please check the materials and try again."
            )
            return None
        return [material_info.url for material_info in materials]
    else:
        logger.info(f"\n\n## downloading videos from {params.video_source}")
        downloaded_videos = material.download_videos(
            task_id=task_id,
            search_terms=video_terms,
            source=params.video_source,
            video_aspect=params.video_aspect,
            video_contact_mode=params.video_concat_mode,
            audio_duration=audio_duration * params.video_count,
            max_clip_duration=params.video_clip_duration,
        )
        if not downloaded_videos:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error(
                "failed to download videos, maybe the network is not available. if you are in China, please use a VPN."
            )
            return None
        return downloaded_videos


def generate_final_videos(
    task_id, params, downloaded_videos, audio_file, subtitle_path
):
    final_video_paths = []
    combined_video_paths = []
    video_concat_mode = (
        params.video_concat_mode if params.video_count == 1 else VideoConcatMode.random
    )
    video_transition_mode = params.video_transition_mode

    _progress = 50
    for i in range(params.video_count):
        index = i + 1
        combined_video_path = path.join(
            utils.task_dir(task_id), f"combined-{index}.mp4"
        )
        logger.info(f"\n\n## combining video: {index} => {combined_video_path}")
        video.combine_videos(
            combined_video_path=combined_video_path,
            video_paths=downloaded_videos,
            audio_file=audio_file,
            video_aspect=params.video_aspect,
            video_concat_mode=video_concat_mode,
            video_transition_mode=video_transition_mode,
            max_clip_duration=params.video_clip_duration,
            threads=params.n_threads,
        )

        _progress += 50 / params.video_count / 2
        sm.state.update_task(task_id, progress=_progress)

        final_video_path = path.join(
            utils.task_dir(task_id),
            _final_video_filename(params.video_subject, index),
        )

        logger.info(f"\n\n## generating video: {index} => {final_video_path}")
        video.generate_video(
            video_path=combined_video_path,
            audio_path=audio_file,
            subtitle_path=subtitle_path,
            output_file=final_video_path,
            params=params,
        )

        _progress += 50 / params.video_count / 2
        sm.state.update_task(task_id, progress=_progress)

        final_video_paths.append(final_video_path)
        combined_video_paths.append(combined_video_path)

    return final_video_paths, combined_video_paths


def queue_buffer_tiktok_posts(
    task_id: str,
    params: VideoParams,
    final_video_paths: list[str],
    scripts: list[str] | None = None,
) -> list[dict]:
    if not (
        buffer_post.buffer_post_service.is_configured()
        and buffer_post.buffer_post_service.auto_queue
    ):
        return []

    logger.info("\n\n## queueing videos to Buffer TikTok")
    results = []
    scripts = scripts or []
    for index, video_path in enumerate(final_video_paths, start=1):
        video_script = scripts[index - 1] if index <= len(scripts) else ""
        result = buffer_post.queue_tiktok_video(
            task_id=task_id,
            video_path=video_path,
            title=params.video_subject,
            video_script=video_script,
            version_index=index if len(final_video_paths) > 1 else None,
        )
        results.append(result)
        if result.get("success"):
            post = result.get("post") or {}
            logger.info(f"Queued Buffer TikTok post: {post.get('id')} for {video_path}")
        else:
            logger.warning(
                f"Failed to queue Buffer TikTok post for {video_path}: "
                f"{result.get('error', 'Unknown error')}"
            )

    return results


def _normalize_version_count(video_count) -> int:
    try:
        count = int(video_count or _VERSION_COUNT_DEFAULT)
    except (TypeError, ValueError):
        count = _VERSION_COUNT_DEFAULT
    return max(1, min(_VERSION_COUNT_MAX, count))


def _version_style(version_index: int) -> dict:
    return _VERSION_STYLES[(version_index - 1) % len(_VERSION_STYLES)]


def _clone_params_for_version(params: VideoParams, version_index: int) -> VideoParams:
    version_params = copy.deepcopy(params)
    style = _version_style(version_index)
    version_params.video_count = 1
    version_params.video_concat_mode = style["concat_mode"]
    version_params.video_transition_mode = style["transition_mode"]
    base_duration = int(params.video_clip_duration or 5)
    version_params.video_clip_duration = max(
        2, min(10, base_duration + int(style["clip_delta"]))
    )
    return version_params


def _build_variant_prompt(params: VideoParams, version_index: int, total_versions: int) -> str:
    style = _version_style(version_index)
    prompt_parts = []
    if params.video_script_prompt:
        prompt_parts.append(params.video_script_prompt.strip())

    prompt_parts.append(
        "\n".join(
            [
                f"Create version {version_index} of {total_versions}.",
                f"Version style: {style['name']}. {style['prompt']}",
                "Make this version meaningfully different from the other versions.",
                "Use a different hook, sentence rhythm, examples, and final takeaway.",
                "Avoid repeating the same opening sentence or paragraph structure.",
            ]
        )
    )

    if params.video_script and version_index > 1:
        prompt_parts.append(
            "\n".join(
                [
                    "Use this existing script only as source material.",
                    "Rewrite it as a fresh alternate version instead of copying it:",
                    params.video_script.strip(),
                ]
            )
        )

    return "\n\n".join(part for part in prompt_parts if part)


def generate_version_script(task_id, params, version_index: int, total_versions: int):
    if params.video_script and version_index == 1:
        logger.info("\n\n## using provided script for version 1")
        return params.video_script.strip()

    logger.info(f"\n\n## generating video script version {version_index}/{total_versions}")
    video_script = llm.generate_script(
        video_subject=params.video_subject,
        language=params.video_language,
        paragraph_number=params.paragraph_number,
        video_script_prompt=_build_variant_prompt(params, version_index, total_versions),
        custom_system_prompt=params.custom_system_prompt,
    )

    if not video_script:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error(f"failed to generate video script version {version_index}.")
        return None

    return video_script


def save_versioned_script_data(task_id, versions, params):
    script_file = path.join(utils.task_dir(task_id), "script.json")
    script_data = {
        "versions": versions,
        "params": params,
    }

    with open(script_file, "w", encoding="utf-8") as f:
        f.write(utils.to_json(script_data))


def generate_single_final_video(
    task_id,
    params,
    downloaded_videos,
    audio_file,
    subtitle_path,
    version_index: int,
):
    combined_video_path = path.join(utils.task_dir(task_id), f"combined-{version_index}.mp4")
    final_video_path = path.join(
        utils.task_dir(task_id),
        _final_video_filename(params.video_subject, version_index),
    )

    logger.info(
        f"\n\n## combining video version {version_index} => {combined_video_path}"
    )
    video.combine_videos(
        combined_video_path=combined_video_path,
        video_paths=downloaded_videos,
        audio_file=audio_file,
        video_aspect=params.video_aspect,
        video_concat_mode=params.video_concat_mode,
        video_transition_mode=params.video_transition_mode,
        max_clip_duration=params.video_clip_duration,
        threads=params.n_threads,
    )

    logger.info(f"\n\n## generating video version {version_index} => {final_video_path}")
    video.generate_video(
        video_path=combined_video_path,
        audio_path=audio_file,
        subtitle_path=subtitle_path,
        output_file=final_video_path,
        params=params,
    )

    return final_video_path, combined_video_path


def start_versioned_videos(task_id, params: VideoParams):
    total_versions = _normalize_version_count(params.video_count)
    abort_if_cancelled(task_id)
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=5)

    final_video_paths = []
    combined_video_paths = []
    scripts = []
    terms_list = []
    audio_files = []
    audio_durations = []
    subtitle_paths = []
    materials_list = []
    version_records = []

    if type(params.video_concat_mode) is str:
        params.video_concat_mode = VideoConcatMode(params.video_concat_mode)

    for index in range(1, total_versions + 1):
        abort_if_cancelled(task_id)
        version_params = _clone_params_for_version(params, index)
        style = _version_style(index)
        logger.info(
            f"\n\n## generating version {index}/{total_versions}, style: {style['name']}"
        )

        version_progress_base = 5 + ((index - 1) / total_versions) * 90
        version_progress_span = 90 / total_versions

        video_script = generate_version_script(task_id, params, index, total_versions)
        abort_if_cancelled(task_id)
        if not video_script or "Error: " in video_script:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
        scripts.append(video_script)
        sm.state.update_task(
            task_id, progress=version_progress_base + version_progress_span * 0.15
        )

        video_terms = ""
        if version_params.video_source != "local":
            abort_if_cancelled(task_id)
            video_terms = generate_terms(task_id, version_params, video_script)
            abort_if_cancelled(task_id)
            if not video_terms:
                sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
                return
        terms_list.append(video_terms)

        audio_file, audio_duration, sub_maker = generate_audio(
            task_id, version_params, video_script, version_index=index
        )
        abort_if_cancelled(task_id)
        if not audio_file:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
        audio_files.append(audio_file)
        audio_durations.append(audio_duration)
        sm.state.update_task(
            task_id, progress=version_progress_base + version_progress_span * 0.35
        )

        subtitle_path = generate_subtitle(
            task_id,
            version_params,
            video_script,
            sub_maker,
            audio_file,
            version_index=index,
        )
        abort_if_cancelled(task_id)
        subtitle_paths.append(subtitle_path)

        downloaded_videos = get_video_materials(
            task_id, version_params, video_terms, audio_duration
        )
        abort_if_cancelled(task_id)
        if not downloaded_videos:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return
        materials_list.append(downloaded_videos)
        sm.state.update_task(
            task_id, progress=version_progress_base + version_progress_span * 0.55
        )

        final_video_path, combined_video_path = generate_single_final_video(
            task_id,
            version_params,
            downloaded_videos,
            audio_file,
            subtitle_path,
            index,
        )
        abort_if_cancelled(task_id)
        final_video_paths.append(final_video_path)
        combined_video_paths.append(combined_video_path)
        sm.state.update_task(
            task_id, progress=version_progress_base + version_progress_span * 0.95
        )

        version_records.append(
            {
                "index": index,
                "style": style["name"],
                "script": video_script,
                "search_terms": video_terms,
                "audio_file": audio_file,
                "audio_duration": audio_duration,
                "subtitle_path": subtitle_path,
                "materials": downloaded_videos,
                "video": final_video_path,
                "combined_video": combined_video_path,
            }
        )

    save_versioned_script_data(task_id, version_records, params)
    abort_if_cancelled(task_id)

    logger.success(
        f"task {task_id} finished, generated {len(final_video_paths)} video versions."
    )

    cross_post_results = []
    if upload_post.upload_post_service.is_configured() and upload_post.upload_post_service.auto_upload:
        logger.info("\n\n## cross-posting videos to TikTok/Instagram")
        for video_path in final_video_paths:
            abort_if_cancelled(task_id)
            result = upload_post.cross_post_video(
                video_path=video_path,
                title=params.video_subject or "Check out this video! #shorts #viral",
            )
            cross_post_results.append(result)
            if result.get("success"):
                logger.info(f"✅ Cross-posted: {video_path}")
            else:
                logger.warning(
                    f"⚠️ Failed to cross-post: {video_path} - {result.get('error', 'Unknown error')}"
                )

    abort_if_cancelled(task_id)
    buffer_post_results = queue_buffer_tiktok_posts(
        task_id=task_id,
        params=params,
        final_video_paths=final_video_paths,
        scripts=scripts,
    )

    kwargs = {
        "videos": final_video_paths,
        "combined_videos": combined_video_paths,
        "script": scripts[0] if scripts else "",
        "scripts": scripts,
        "terms": terms_list[0] if terms_list else "",
        "terms_list": terms_list,
        "audio_file": audio_files[0] if audio_files else "",
        "audio_files": audio_files,
        "audio_duration": audio_durations[0] if audio_durations else 0,
        "audio_durations": audio_durations,
        "subtitle_path": subtitle_paths[0] if subtitle_paths else "",
        "subtitle_paths": subtitle_paths,
        "materials": materials_list[0] if materials_list else "",
        "materials_list": materials_list,
        "versions": version_records,
        "cross_post_results": cross_post_results if cross_post_results else None,
        "buffer_post_results": buffer_post_results if buffer_post_results else None,
    }
    sm.state.update_task(
        task_id, state=const.TASK_STATE_COMPLETE, progress=100, **kwargs
    )
    return kwargs


def start(task_id, params: VideoParams, stop_at: str = "video"):
    logger.info(f"start task: {task_id}, stop_at: {stop_at}")
    try:
        abort_if_cancelled(task_id)
        if stop_at == "video":
            return start_versioned_videos(task_id, params)

        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=5)

        # 1. Generate script
        video_script = generate_script(task_id, params)
        abort_if_cancelled(task_id)
        if not video_script or "Error: " in video_script:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=10)

        if stop_at == "script":
            sm.state.update_task(
                task_id, state=const.TASK_STATE_COMPLETE, progress=100, script=video_script
            )
            return {"script": video_script}

        # 2. Generate terms
        video_terms = ""
        if params.video_source != "local":
            video_terms = generate_terms(task_id, params, video_script)
            abort_if_cancelled(task_id)
            if not video_terms:
                sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
                return

        save_script_data(task_id, video_script, video_terms, params)
        abort_if_cancelled(task_id)

        if stop_at == "terms":
            sm.state.update_task(
                task_id, state=const.TASK_STATE_COMPLETE, progress=100, terms=video_terms
            )
            return {"script": video_script, "terms": video_terms}

        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=20)

        # 3. Generate audio
        audio_file, audio_duration, sub_maker = generate_audio(
            task_id, params, video_script
        )
        abort_if_cancelled(task_id)
        if not audio_file:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=30)

        if stop_at == "audio":
            sm.state.update_task(
                task_id,
                state=const.TASK_STATE_COMPLETE,
                progress=100,
                audio_file=audio_file,
            )
            return {"audio_file": audio_file, "audio_duration": audio_duration}

        # 4. Generate subtitle
        subtitle_path = generate_subtitle(
            task_id, params, video_script, sub_maker, audio_file
        )
        abort_if_cancelled(task_id)

        if stop_at == "subtitle":
            sm.state.update_task(
                task_id,
                state=const.TASK_STATE_COMPLETE,
                progress=100,
                subtitle_path=subtitle_path,
            )
            return {"subtitle_path": subtitle_path}

        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=40)

        # 5. Get video materials
        downloaded_videos = get_video_materials(
            task_id, params, video_terms, audio_duration
        )
        abort_if_cancelled(task_id)
        if not downloaded_videos:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

        if stop_at == "materials":
            sm.state.update_task(
                task_id,
                state=const.TASK_STATE_COMPLETE,
                progress=100,
                materials=downloaded_videos,
            )
            return {"materials": downloaded_videos}

        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=50)

        # 仅完整视频生成流程才需要处理视频拼接模式；
        # 这样可以避免 /subtitle 和 /audio 这类请求访问不存在的字段。
        if type(params.video_concat_mode) is str:
            params.video_concat_mode = VideoConcatMode(params.video_concat_mode)

        # 6. Generate final videos
        final_video_paths, combined_video_paths = generate_final_videos(
            task_id, params, downloaded_videos, audio_file, subtitle_path
        )
        abort_if_cancelled(task_id)

        if not final_video_paths:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

        logger.success(
            f"task {task_id} finished, generated {len(final_video_paths)} videos."
        )

        # 7. Cross-post to TikTok/Instagram (if enabled)
        cross_post_results = []
        if upload_post.upload_post_service.is_configured() and upload_post.upload_post_service.auto_upload:
            logger.info("\n\n## cross-posting videos to TikTok/Instagram")
            for video_path in final_video_paths:
                abort_if_cancelled(task_id)
                result = upload_post.cross_post_video(
                    video_path=video_path,
                    title=params.video_subject or "Check out this video! #shorts #viral"
                )
                cross_post_results.append(result)
                if result.get('success'):
                    logger.info(f"✅ Cross-posted: {video_path}")
                else:
                    logger.warning(f"⚠️ Failed to cross-post: {video_path} - {result.get('error', 'Unknown error')}")

        abort_if_cancelled(task_id)
        buffer_post_results = queue_buffer_tiktok_posts(
            task_id=task_id,
            params=params,
            final_video_paths=final_video_paths,
            scripts=[video_script],
        )

        kwargs = {
            "videos": final_video_paths,
            "combined_videos": combined_video_paths,
            "script": video_script,
            "terms": video_terms,
            "audio_file": audio_file,
            "audio_duration": audio_duration,
            "subtitle_path": subtitle_path,
            "materials": downloaded_videos,
            "cross_post_results": cross_post_results if cross_post_results else None,
            "buffer_post_results": buffer_post_results if buffer_post_results else None,
        }
        sm.state.update_task(
            task_id, state=const.TASK_STATE_COMPLETE, progress=100, **kwargs
        )
        return kwargs
    except TaskCancelled:
        sm.state.update_task(task_id, state=const.TASK_STATE_CANCELED)
        return {"canceled": True}
    finally:
        clear_cancel_task(task_id)


if __name__ == "__main__":
    task_id = "task_id"
    params = VideoParams(
        video_subject="金钱的作用",
        voice_name="zh-CN-XiaoyiNeural-Female",
        voice_rate=1.0,
    )
    start(task_id, params, stop_at="video")
