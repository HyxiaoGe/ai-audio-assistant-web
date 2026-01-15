"""转写文本预处理和质量评估工具

提供转写文本的质量评估和预处理功能，用于提升摘要生成质量。
"""

from typing import Any, Optional

from app.services.asr.base import TranscriptSegment


class TranscriptQuality:
    """转写质量评估结果"""

    def __init__(
        self,
        quality_score: str,
        avg_confidence: float,
        low_confidence_count: int,
        low_confidence_ratio: float,
    ):
        self.quality_score = quality_score  # "high", "medium", "low"
        self.avg_confidence = avg_confidence  # 平均置信度 0.0-1.0
        self.low_confidence_count = low_confidence_count  # 低置信度片段数量
        self.low_confidence_ratio = low_confidence_ratio  # 低置信度片段占比


class TranscriptProcessor:
    """转写文本处理器

    提供转写文本的质量评估和预处理功能。
    """

    # 常见语气词列表
    FILLER_WORDS = {"嗯", "啊", "呃", "额", "哦", "嗷", "唉", "诶", "哎"}

    # 质量阈值
    LOW_CONFIDENCE_THRESHOLD = 0.7  # 低于此值视为低置信度
    QUALITY_THRESHOLDS = {
        "high": 0.8,  # >= 0.8 为高质量
        "medium": 0.6,  # >= 0.6 为中等质量
        # < 0.6 为低质量
    }

    @classmethod
    def assess_quality(cls, segments: list[TranscriptSegment]) -> TranscriptQuality:
        """评估转写质量

        Args:
            segments: 转写片段列表

        Returns:
            TranscriptQuality: 质量评估结果
        """
        if not segments:
            return TranscriptQuality("low", 0.0, 0, 1.0)

        total_segments = len(segments)
        segments_with_confidence = [s for s in segments if s.confidence is not None]

        if not segments_with_confidence:
            # 没有置信度信息，假设为中等质量
            return TranscriptQuality("medium", 0.75, 0, 0.0)

        # 计算平均置信度
        avg_confidence = sum(s.confidence for s in segments_with_confidence) / len(
            segments_with_confidence
        )

        # 统计低置信度片段
        low_confidence_segments = [
            s for s in segments_with_confidence if s.confidence < cls.LOW_CONFIDENCE_THRESHOLD
        ]
        low_confidence_count = len(low_confidence_segments)
        low_confidence_ratio = low_confidence_count / total_segments

        # 判断质量等级
        if avg_confidence >= cls.QUALITY_THRESHOLDS["high"]:
            quality_score = "high"
        elif avg_confidence >= cls.QUALITY_THRESHOLDS["medium"]:
            quality_score = "medium"
        else:
            quality_score = "low"

        return TranscriptQuality(
            quality_score=quality_score,
            avg_confidence=avg_confidence,
            low_confidence_count=low_confidence_count,
            low_confidence_ratio=low_confidence_ratio,
        )

    @classmethod
    def preprocess(
        cls,
        segments: list[TranscriptSegment],
        filter_filler_words: bool = True,
        merge_same_speaker: bool = True,
        merge_threshold_seconds: float = 2.0,
    ) -> str:
        """预处理转写文本

        执行以下操作：
        1. 过滤低置信度的语气词
        2. 合并同一说话人的连续segment（时间间隔短）
        3. 格式化为便于LLM理解的文本

        Args:
            segments: 转写片段列表
            filter_filler_words: 是否过滤语气词
            merge_same_speaker: 是否合并同一说话人的连续segment
            merge_threshold_seconds: 合并阈值（秒），小于此值的间隔会被合并

        Returns:
            str: 预处理后的文本
        """
        if not segments:
            return ""

        # Step 1: 过滤语气词
        filtered_segments = []
        for seg in segments:
            if filter_filler_words and cls._is_filler_word(seg):
                continue  # 跳过语气词
            filtered_segments.append(seg)

        if not filtered_segments:
            return ""

        # Step 2: 合并同一说话人的连续segment
        if merge_same_speaker:
            merged_blocks = cls._merge_segments(filtered_segments, merge_threshold_seconds)
        else:
            # 不合并，直接转换为block格式
            merged_blocks = [
                {
                    "speaker_id": seg.speaker_id,
                    "start_time": seg.start_time,
                    "end_time": seg.end_time,
                    "content": seg.content,
                }
                for seg in filtered_segments
            ]

        # Step 3: 格式化为文本
        formatted_text = cls._format_blocks(merged_blocks)

        return formatted_text

    @classmethod
    def _is_filler_word(cls, segment: TranscriptSegment) -> bool:
        """判断是否为语气词

        过滤条件：
        1. 低置信度（< 0.7）
        2. 内容很短（<= 2个字符）
        3. 是常见语气词

        Args:
            segment: 转写片段

        Returns:
            bool: 是否为语气词
        """
        content = segment.content.strip()

        # 检查是否满足所有条件
        is_low_confidence = segment.confidence is not None and segment.confidence < 0.7
        is_short = len(content) <= 2
        is_filler = content in cls.FILLER_WORDS

        return is_low_confidence and is_short and is_filler

    @classmethod
    def _merge_segments(
        cls, segments: list[TranscriptSegment], threshold_seconds: float
    ) -> list[dict[str, Any]]:
        """合并同一说话人的连续segment

        Args:
            segments: 转写片段列表
            threshold_seconds: 时间间隔阈值（秒）

        Returns:
            list[dict]: 合并后的block列表
        """
        merged: list[dict[str, Any]] = []
        current_block: Optional[dict[str, Any]] = None

        for seg in segments:
            if current_block is None:
                # 第一个segment
                current_block = {
                    "speaker_id": seg.speaker_id,
                    "start_time": seg.start_time,
                    "end_time": seg.end_time,
                    "content": [seg.content],
                }
                continue

            # 判断是否需要开启新block
            should_start_new = (
                seg.speaker_id != current_block["speaker_id"]  # 说话人变化
                or (seg.start_time - current_block["end_time"]) > threshold_seconds  # 时间间隔过长
            )

            if should_start_new:
                # 保存当前block
                merged.append(
                    {
                        "speaker_id": current_block["speaker_id"],
                        "start_time": current_block["start_time"],
                        "end_time": current_block["end_time"],
                        "content": " ".join(current_block["content"]),
                    }
                )

                # 开始新block
                current_block = {
                    "speaker_id": seg.speaker_id,
                    "start_time": seg.start_time,
                    "end_time": seg.end_time,
                    "content": [seg.content],
                }
            else:
                # 继续当前block
                current_block["content"].append(seg.content)
                current_block["end_time"] = seg.end_time

        # 保存最后一个block
        if current_block is not None:
            merged.append(
                {
                    "speaker_id": current_block["speaker_id"],
                    "start_time": current_block["start_time"],
                    "end_time": current_block["end_time"],
                    "content": " ".join(current_block["content"]),
                }
            )

        return merged

    @classmethod
    def _format_blocks(cls, blocks: list[dict[str, Any]]) -> str:
        """格式化blocks为文本

        格式示例：
        [speaker_0] 大家好今天我们讨论一下用户增长的问题

        [speaker_1] 好的我觉得我们应该先看一下数据 然后再制定策略

        Args:
            blocks: block列表

        Returns:
            str: 格式化后的文本
        """
        lines = []
        for block in blocks:
            speaker_label = block["speaker_id"] or "Speaker"
            content = block["content"]
            lines.append(f"[{speaker_label}] {content}")

        # 用双换行分隔不同block，便于LLM识别段落
        return "\n\n".join(lines)

    @classmethod
    def get_quality_notice(cls, quality: TranscriptQuality) -> str:
        """生成质量提示文本

        用于在提示词中告知LLM转写质量情况

        Args:
            quality: 质量评估结果

        Returns:
            str: 质量提示文本
        """
        if quality.quality_score == "low":
            return f"""【重要提示】
此转写文本质量较低（平均置信度：{quality.avg_confidence:.2f}），可能存在较多识别错误。
请特别注意：
1. 根据上下文推断正确含义
2. 纠正明显的同音词错误（如"增长"→"曾长"）
3. 忽略识别错误，聚焦语义理解
4. 优先提取有把握的核心信息"""

        elif quality.quality_score == "medium":
            return """【说明】
以下转写文本来自语音识别，可能存在部分识别错误（如同音词混淆、标点不规范等）。
请根据上下文理解文本真实含义，聚焦核心信息提取。"""

        else:  # high quality
            return """【说明】
以下转写文本来自语音识别，可能存在少量识别错误。
请根据上下文理解文本真实含义。"""
