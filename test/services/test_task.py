import unittest
import os
import sys
from pathlib import Path
from unittest.mock import patch

# add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import task as tm
from app.models.schema import MaterialInfo, VideoParams

resources_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")

class TestTaskService(unittest.TestCase):
    def setUp(self):
        pass
    
    def tearDown(self):
        pass

    def test_generate_script_forwards_advanced_prompt_options(self):
        """
        任务生成入口和 WebUI/API 共用 VideoParams。这里验证自动生成文案时，
        高级提示词参数会继续传到 LLM 服务层，避免只在 /scripts 接口生效。
        """
        params = VideoParams(
            video_subject="咖啡",
            video_script="",
            video_language="zh-CN",
            paragraph_number=2,
            video_script_prompt="语气轻松",
            custom_system_prompt="Only write short narration.",
        )

        with patch.object(tm.llm, "generate_script", return_value="生成的文案") as generate:
            result = tm.generate_script("task-id", params)

        self.assertEqual(result, "生成的文案")
        generate.assert_called_once_with(
            video_subject="咖啡",
            language="zh-CN",
            paragraph_number=2,
            video_script_prompt="语气轻松",
            custom_system_prompt="Only write short narration.",
        )

    def test_start_video_generates_independent_versions(self):
        params = VideoParams(
            video_subject="du lịch Đà Nẵng",
            video_script="",
            video_terms=None,
            video_count=5,
            video_source="pexels",
            voice_name="vi-VN-NamMinhNeural-Male",
            video_clip_duration=3,
            video_concat_mode="random",
        )

        scripts = [f"script version {i}" for i in range(1, 6)]

        def fake_audio(task_id, version_params, video_script, version_index=None):
            return f"/tmp/audio-{version_index}.mp3", 10 + version_index, object()

        def fake_subtitle(task_id, version_params, video_script, sub_maker, audio_file, version_index=None):
            return f"/tmp/subtitle-{version_index}.srt"

        def fake_materials(task_id, version_params, video_terms, audio_duration):
            return [f"/tmp/material-{len(video_terms)}-{audio_duration}.mp4"]

        def fake_final(task_id, version_params, downloaded_videos, audio_file, subtitle_path, version_index):
            return f"/tmp/final-{version_index}.mp4", f"/tmp/combined-{version_index}.mp4"

        with patch.object(tm.llm, "generate_script", side_effect=scripts), patch.object(
            tm, "generate_terms", side_effect=lambda task_id, params, script: [f"term-{script}"]
        ), patch.object(tm, "generate_audio", side_effect=fake_audio), patch.object(
            tm, "generate_subtitle", side_effect=fake_subtitle
        ), patch.object(tm, "get_video_materials", side_effect=fake_materials), patch.object(
            tm, "generate_single_final_video", side_effect=fake_final
        ), patch.object(tm, "save_versioned_script_data"), patch.object(
            tm.upload_post.upload_post_service, "is_configured", return_value=False
        ):
            result = tm.start("version-task", params)

        self.assertEqual(len(result["videos"]), 5)
        self.assertEqual(len(result["scripts"]), 5)
        self.assertEqual(len(result["audio_files"]), 5)
        self.assertEqual(len(result["subtitle_paths"]), 5)
        self.assertEqual(len(result["materials_list"]), 5)
        self.assertEqual(result["scripts"], scripts)
        self.assertNotEqual(result["versions"][0]["style"], result["versions"][1]["style"])

    def test_final_video_filename_uses_video_subject(self):
        self.assertEqual(
            tm._final_video_filename("du lịch Đà Nẵng 2026!", 3),
            "du-lich-da-nang-2026-3.mp4",
        )

        self.assertEqual(tm._final_video_filename("", 1), "video-1.mp4")

    def test_generate_single_final_video_uses_subject_filename(self):
        params = VideoParams(
            video_subject="du lịch Đà Nẵng",
            video_aspect="9:16",
            video_concat_mode="random",
            video_transition_mode=None,
            video_clip_duration=3,
            n_threads=1,
        )

        with patch.object(tm.video, "combine_videos"), patch.object(
            tm.video, "generate_video"
        ) as generate_video:
            final_path, combined_path = tm.generate_single_final_video(
                task_id="filename-task",
                params=params,
                downloaded_videos=["/tmp/material.mp4"],
                audio_file="/tmp/audio.mp3",
                subtitle_path="/tmp/subtitle.srt",
                version_index=2,
            )

        self.assertTrue(final_path.endswith("du-lich-da-nang-2.mp4"))
        self.assertTrue(combined_path.endswith("combined-2.mp4"))
        self.assertEqual(generate_video.call_args.kwargs["output_file"], final_path)
    
    def test_task_local_materials(self):
        task_id = "00000000-0000-0000-0000-000000000000"
        video_materials=[]
        for i in range(1, 4):
            video_materials.append(MaterialInfo(
                provider="local",
                url=os.path.join(resources_dir, f"{i}.png"),
                duration=0
            ))

        params = VideoParams(
            video_subject="金钱的作用",
            video_script="金钱不仅是交换媒介，更是社会资源的分配工具。它能满足基本生存需求，如食物和住房，也能提供教育、医疗等提升生活品质的机会。拥有足够的金钱意味着更多选择权，比如职业自由或创业可能。但金钱的作用也有边界，它无法直接购买幸福、健康或真诚的人际关系。过度追逐财富可能导致价值观扭曲，忽视精神层面的需求。理想的状态是理性看待金钱，将其作为实现目标的工具而非终极目的。",
            video_terms="money importance, wealth and society, financial freedom, money and happiness, role of money",
            video_aspect="9:16",
            video_concat_mode="random",
            video_transition_mode="None",
            video_clip_duration=3,
            video_count=1,
            video_source="local",
            video_materials=video_materials,
            video_language="",
            voice_name="zh-CN-XiaoxiaoNeural-Female",
            voice_volume=1.0,
            voice_rate=1.0,
            bgm_type="random",
            bgm_file="",
            bgm_volume=0.2,
            subtitle_enabled=True,
            subtitle_position="bottom",
            custom_position=70.0,
            font_name="MicrosoftYaHeiBold.ttc",
            text_fore_color="#FFFFFF",
            text_background_color=True,
            font_size=60,
            stroke_color="#000000",
            stroke_width=1.5,
            n_threads=2,
            paragraph_number=1
        )
        result = tm.start(task_id=task_id, params=params)
        print(result)
    

if __name__ == "__main__":
    unittest.main()
