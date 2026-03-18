#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RNViewer - Web UI

基于 Streamlit 构建的笔记浏览界面，支持：
- 搜索笔记
- 按标签查看
- 笔记详情（Markdown 渲染）
- 统计信息
- 导出功能

运行方式：
    streamlit run app.py

开发者：De-hamster
"""

import streamlit as st
import streamlit.components.v1 as components
import sys
import os
import re
import json
import base64
import shutil
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))
from lib.reminds import RemindsDB, Memo, Tag, search, get_tag_memos, get_untagged_memos, get_memo, get_memo_by_uuid, list_tags, stats

# ============== 图片处理 ==============
def get_reminds_img_path() -> Path:
    """
    根据 reminds.db 数据库路径动态计算图片目录

    图片目录规则：数据库所在目录的 statics/img 子目录
    例如：db_path = C:/Users/xxx/AppData/Roaming/reminds/reminds.db
         img_path = C:/Users/xxx/AppData/Roaming/reminds/statics/img
    """
    # 优先从配置获取数据库路径
    config = st.session_state.get('config', {})
    db_path = config.get('db_path', '')

    if db_path:
        db_dir = Path(db_path).parent
        return db_dir / 'statics' / 'img'

    # 回退到默认路径
    return Path(os.environ.get('APPDATA', '')) / 'reminds' / 'statics' / 'img'


@st.cache_data(show_spinner=False)
def _convert_reminds_images_cached(md_content: str, img_path_str: str, _cache_version: str = "v2") -> str:
    """
    缓存的图片转换函数（内部实现）

    Args:
        md_content: Markdown 内容
        img_path_str: 图片目录路径字符串

    Returns:
        转换后的内容
    """
    if not md_content:
        return md_content

    reminds_img_path = Path(img_path_str)

    def replace_image(match):
        full_match = match.group(0)
        alt_text = match.group(1)
        img_url = match.group(2)
        title = match.group(3) if match.lastindex >= 3 else ''

        # 检查是否是 reminds://img/ 或 提醒://img/ 协议
        relative_path = None
        if img_url.startswith('reminds://img/'):
            relative_path = img_url.replace('reminds://img/', '')
        elif img_url.startswith('提醒://img/'):
            relative_path = img_url.replace('提醒://img/', '')

        if relative_path:
            local_path = reminds_img_path / relative_path

            if local_path.exists():
                try:
                    # 读取图片并转换为 base64
                    with open(local_path, 'rb') as f:
                        img_data = f.read()

                    # 获取图片类型
                    suffix = local_path.suffix.lower()
                    mime_types = {
                        '.jpg': 'image/jpeg',
                        '.jpeg': 'image/jpeg',
                        '.png': 'image/png',
                        '.gif': 'image/gif',
                        '.webp': 'image/webp',
                        '.bmp': 'image/bmp',
                    }
                    mime_type = mime_types.get(suffix, 'image/jpeg')

                    # 转换为 base64
                    b64_data = base64.b64encode(img_data).decode('utf-8')
                    data_url = f'data:{mime_type};base64,{b64_data}'

                    # 重建 markdown 图片标签
                    if title:
                        return f'![{alt_text}]({data_url} "{title}")'
                    else:
                        return f'![{alt_text}]({data_url})'
                except Exception as e:
                    print(f"Error loading image {local_path}: {e}")
                    return full_match

        return full_match

    # 匹配 markdown 图片语法: ![alt](url "title") 或 ![alt](url)
    pattern = r'!\[([^\]]*)\]\(([^)]+?)(?:\s+"([^"]+)")?\)'
    return re.sub(pattern, replace_image, md_content)


def convert_reminds_images(md_content: str) -> str:
    """将 reminds://img/ 协议图片转换为 base64 data URL（带缓存）"""
    if not md_content:
        return md_content

    reminds_img_path = get_reminds_img_path()
    return _convert_reminds_images_cached(md_content, str(reminds_img_path))


def extract_internal_links(md_content: str) -> list:
    """
    提取笔记中的内部链接

    Args:
        md_content: Markdown 内容

    Returns:
        链接列表，每项为 (link_text, uuid, title)
    """
    if not md_content:
        return []

    links = []
    # 匹配格式: [链接文字](/memo/uuid "标题")
    pattern = r'\[([^\]]+)\]\(/memo/([a-f0-9-]+)(?:\s+"([^"]+)")?\)'
    for match in re.finditer(pattern, md_content):
        link_text = match.group(1)
        uuid = match.group(2)
        title = match.group(3) or link_text
        links.append((link_text, uuid, title))

    return links


def convert_internal_links_for_st(md_content: str, current_memo_id: int = None) -> str:
    """
    将内部链接转换为可在 st.markdown 中点击的链接

    使用查询参数 ?goto_uuid=xxx&from_memo=yyy 触发页面跳转
    同时保存来源笔记ID，确保导航历史不丢失

    Args:
        md_content: Markdown 内容
        current_memo_id: 当前笔记ID（用于保存到URL参数）

    Returns:
        转换后的内容
    """
    if not md_content:
        return md_content

    # 将 /memo/uuid 链接转换为带有查询参数的链接
    def replace_link(match):
        link_text = match.group(1)
        uuid = match.group(2)
        # 使用查询参数触发跳转，同时保存来源笔记ID
        if current_memo_id:
            return f'[{link_text}](?goto_uuid={uuid}&from_memo={current_memo_id})'
        else:
            return f'[{link_text}](?goto_uuid={uuid})'

    # 更宽松的正则：匹配各种可能的链接格式
    # 支持：[文字](/memo/uuid) 或 [文字](/memo/uuid "标题") 或 [文字](/memo/uuid '标题')
    pattern = r'\[([^\]]+)\]\(/memo/([a-f0-9-]+)(?:\s+["\'][^"\']*["\'])?\s*\)'
    return re.sub(pattern, replace_link, md_content)


def process_export_images(md_content: str, export_path: Path) -> str:
    """处理导出时的图片：复制到导出目录并更新路径"""
    if not md_content:
        return md_content

    # 创建图片目录
    img_dir = export_path.parent / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    reminds_img_path = get_reminds_img_path()

    def replace_image(match):
        full_match = match.group(0)
        alt_text = match.group(1)
        img_url = match.group(2)
        title = match.group(3) if match.lastindex >= 3 else ''

        # 检查是否是 reminds://img/ 协议
        if img_url.startswith('reminds://img/'):
            relative_path = img_url.replace('reminds://img/', '')
            local_path = reminds_img_path / relative_path

            if local_path.exists():
                try:
                    # 生成唯一文件名
                    img_filename = f"{local_path.stem}_{hash(relative_path)}{local_path.suffix}"
                    dest_path = img_dir / img_filename

                    # 复制图片
                    shutil.copy2(local_path, dest_path)

                    # 更新为相对路径
                    new_url = f"images/{img_filename}"
                    if title:
                        return f'![{alt_text}]({new_url} "{title}")'
                    else:
                        return f'![{alt_text}]({new_url})'
                except Exception as e:
                    print(f"Error copying image {local_path}: {e}")
                    return full_match

        return full_match

    # 匹配 markdown 图片语法
    pattern = r'!\[([^\]]*)\]\(([^)]+?)(?:\s+"([^"]+)")?\)'
    return re.sub(pattern, replace_image, md_content)


# ============== 页面配置 ==============
st.set_page_config(
    page_title="RNViewer",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============== 自定义样式 ==============
st.markdown("""
<style>
    /* 主色调 */
    :root {
        --primary-color: #2563EB;
        --secondary-color: #3B82F6;
        --cta-color: #F97316;
        --background-color: #F8FAFC;
        --text-color: #1E293B;
    }

    /* 标题样式 */
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: var(--text-color);
        margin-bottom: 0.5rem;
    }

    /* 卡片样式 */
    .note-card {
        background: white;
        border-radius: 12px;
        padding: 1.25rem;
        margin-bottom: 1rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        border: 1px solid #E2E8F0;
        transition: box-shadow 0.2s ease, transform 0.2s ease;
    }

    .note-card:hover {
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        transform: translateY(-2px);
    }

    /* 标签样式 */
    .tag-badge {
        display: inline-block;
        background: #EFF6FF;
        color: var(--primary-color);
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.875rem;
        margin-right: 0.5rem;
        margin-bottom: 0.5rem;
        cursor: pointer;
        transition: background 0.2s ease;
    }

    .tag-badge:hover {
        background: #DBEAFE;
    }

    /* 统计卡片 */
    .stat-card {
        background: white;
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        border: 1px solid #E2E8F0;
    }

    .stat-number {
        font-size: 2rem;
        font-weight: 700;
        color: var(--primary-color);
    }

    .stat-label {
        font-size: 0.875rem;
        color: #64748B;
        margin-top: 0.25rem;
    }

    /* 搜索框增强 */
    .stTextInput > div > div > input {
        border-radius: 12px;
        border: 2px solid #E2E8F0;
        padding: 0.75rem 1rem;
        font-size: 1rem;
    }

    .stTextInput > div > div > input:focus {
        border-color: var(--primary-color);
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
    }

    /* 侧边栏样式 */
    section[data-testid="stSidebar"] {
        background: #F8FAFC;
    }

    /* 按钮样式 */
    .stButton > button {
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.2s ease;
    }

    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    }

    /* Markdown 内容样式 */
    .markdown-content {
        line-height: 1.75;
        color: var(--text-color);
    }

    .markdown-content h1, .markdown-content h2, .markdown-content h3 {
        color: var(--text-color);
        margin-top: 1.5rem;
        margin-bottom: 0.75rem;
    }

    .markdown-content code {
        background: #F1F5F9;
        padding: 0.2rem 0.4rem;
        border-radius: 4px;
        font-size: 0.875rem;
    }

    .markdown-content pre {
        background: #1E293B;
        color: #E2E8F0;
        padding: 1rem;
        border-radius: 8px;
        overflow-x: auto;
    }

    .markdown-content pre code {
        background: transparent;
        padding: 0;
    }

    /* 空状态 */
    .empty-state {
        text-align: center;
        padding: 3rem;
        color: #64748B;
    }

    .empty-state-icon {
        font-size: 3rem;
        margin-bottom: 1rem;
    }
</style>
""", unsafe_allow_html=True)


# ============== 配置管理 ==============
CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "db_path": str(Path(os.environ.get('APPDATA', '')) / 'reminds' / 'reminds.db'),
    "export_path": "docs",
    "llm": {
        "base_url": "",
        "api_key": "",
        "model_name": "gpt-4o-mini"
    }
}

# ============== 内容提炼配置 ==============
REFINEMENT_MODES = {
    'summary': {
        'name': '综合总结',
        'prompt_template': """请对以下笔记内容进行全面总结和提炼，生成一份结构清晰的综合性报告。

要求：
1. 概括主要内容，提炼核心观点
2. 按主题或逻辑进行分类整理
3. 标注重要信息和关键数据
4. 语言简洁明了，重点突出

笔记内容：
{content}

请生成总结报告："""
    },
    'keypoints': {
        'name': '提取要点',
        'prompt_template': """请从以下笔记中提取关键要点，生成一份要点清单。

要求：
1. 提取最有价值的信息点
2. 每个要点简洁明了，不超过100字
3. 按重要性排序
4. 使用列表格式呈现

笔记内容：
{content}

请提取关键要点："""
    },
    'outline': {
        'name': '生成大纲',
        'prompt_template': """请根据以下笔记内容生成一个结构化大纲，便于后续深入学习和复习。

要求：
1. 按知识体系组织内容层级
2. 每个主题标注关键概念
3. 层次清晰，最多3级结构
4. 可作为学习路线参考

笔记内容：
{content}

请生成学习大纲："""
    },
    'custom': {
        'name': '自定义提示词',
        'prompt_template': None
    }
}

MAX_CONTENT_LENGTH = 50000  # 最大内容字符数


def load_config() -> dict:
    """加载配置文件"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            # 合并默认配置（处理新增配置项）
            for key, value in DEFAULT_CONFIG.items():
                if key not in config:
                    config[key] = value
                elif isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        if sub_key not in config[key]:
                            config[key][sub_key] = sub_value
            return config
        except Exception as e:
            print(f"加载配置失败: {e}")
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    """保存配置文件"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False


# ============== 会话状态初始化 ==============
def init_session_state():
    """初始化会话状态"""
    if 'current_view' not in st.session_state:
        st.session_state.current_view = 'home'
    if 'selected_tag' not in st.session_state:
        st.session_state.selected_tag = None
    if 'selected_memo_id' not in st.session_state:
        st.session_state.selected_memo_id = None
    if 'search_keyword' not in st.session_state:
        st.session_state.search_keyword = ''
    if 'config' not in st.session_state:
        st.session_state.config = load_config()
    if 'memo_history' not in st.session_state:
        st.session_state.memo_history = []  # 笔记导航历史栈
    if 'source_view' not in st.session_state:
        st.session_state.source_view = 'home'  # 进入笔记详情前的来源视图
    if 'source_tag' not in st.session_state:
        st.session_state.source_tag = None  # 进入笔记详情前的来源标签


def save_source_view():
    """保存当前视图作为来源（进入笔记详情前调用）"""
    st.session_state.source_view = st.session_state.current_view
    st.session_state.source_tag = st.session_state.selected_tag


# ============== 数据加载函数 ==============
def get_db_path() -> str:
    """获取当前配置的数据库路径"""
    config = st.session_state.get('config', {})
    return config.get('db_path', DEFAULT_CONFIG['db_path'])


def check_db_exists() -> tuple:
    """
    检查数据库是否存在

    Returns:
        (exists: bool, path: str)
    """
    db_path = get_db_path()
    return Path(db_path).exists(), db_path


def load_stats():
    """加载统计信息"""
    exists, db_path = check_db_exists()
    if not exists:
        return None
    return stats(db_path)


def load_tags():
    """加载所有标签"""
    return list_tags(get_db_path())


def load_memos_by_tag(tag_name: str):
    """加载标签下的笔记"""
    return get_tag_memos(tag_name, get_db_path())


def load_untagged_memos():
    """加载无标签笔记"""
    return get_untagged_memos(get_db_path())


def search_memos(keyword: str):
    """搜索笔记"""
    if not keyword.strip():
        return []
    return search(keyword, get_db_path())


def load_memo_detail(memo_id: int):
    """加载笔记详情"""
    return get_memo(memo_id, get_db_path())


# ============== 内容提炼辅助函数 ==============
def validate_llm_config(config: dict) -> tuple:
    """
    验证 LLM 配置是否完整

    Returns:
        (is_valid, error_message)
    """
    llm_config = config.get('llm', {})
    base_url = llm_config.get('base_url', '').strip()
    api_key = llm_config.get('api_key', '').strip()
    model_name = llm_config.get('model_name', '').strip()

    if not base_url:
        return False, "请先配置 Base URL"
    if not api_key:
        return False, "请先配置 API Key"
    if not model_name:
        return False, "请先配置模型名称"

    return True, None


def build_llm_url(base_url: str) -> str:
    """
    构建 LLM API 的完整 URL

    Args:
        base_url: 基础 URL

    Returns:
        完整的 chat/completions 端点 URL
    """
    url = base_url.rstrip('/')
    if not url.endswith('/chat/completions'):
        if url.endswith('/v1'):
            url = f"{url}/chat/completions"
        elif '/v1/' not in url:
            url = f"{url}/v1/chat/completions"
        else:
            url = f"{url}/chat/completions"
    return url


def call_llm_api(prompt: str, config: dict) -> tuple:
    """
    调用大模型 API

    Args:
        prompt: 提示词
        config: 配置字典

    Returns:
        (success, result_or_error)
    """
    import requests

    llm_config = config.get('llm', {})
    base_url = llm_config.get('base_url', '').rstrip('/')
    api_key = llm_config.get('api_key', '')
    model_name = llm_config.get('model_name', 'gpt-4o-mini')

    # 构建 API 端点
    url = build_llm_url(base_url)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 4000
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120)

        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0].get('message', {}).get('content', '')
                return True, content
            else:
                return False, "API 返回格式异常"
        else:
            error_msg = response.text[:500]
            if response.status_code == 401:
                return False, "认证失败，请检查 API Key"
            elif response.status_code == 404:
                return False, f"端点不存在，请检查 Base URL 格式"
            else:
                return False, f"API 错误 ({response.status_code}): {error_msg}"

    except requests.exceptions.Timeout:
        return False, "请求超时，请稍后重试"
    except requests.exceptions.ConnectionError:
        return False, "无法连接到服务器，请检查网络"
    except Exception as e:
        return False, f"请求失败: {str(e)}"


def prepare_content_for_refinement(memos: list) -> str:
    """
    准备提炼内容，控制长度

    Args:
        memos: 笔记列表

    Returns:
        格式化的内容字符串
    """
    content_parts = []
    total_length = 0

    for i, memo in enumerate(memos, 1):
        title = memo.title or "无标题"
        memo_content = memo.md_content or ""

        # 格式化单条笔记
        part = f"\n### 笔记 {i}: {title}\n\n{memo_content}\n\n---\n"
        part_length = len(part)

        # 检查总长度限制
        if total_length + part_length > MAX_CONTENT_LENGTH:
            # 添加截断提示
            remaining = MAX_CONTENT_LENGTH - total_length
            if remaining > 100:
                truncated = part[:remaining] + "\n\n... (内容已截断)"
                content_parts.append(truncated)
            break

        content_parts.append(part)
        total_length += part_length

    return "".join(content_parts)


def build_refinement_prompt(content: str, mode: str, tag_name: str, custom_prompt: str = None) -> str:
    """
    构建提炼提示词

    Args:
        content: 笔记内容
        mode: 提炼模式
        tag_name: 标签名
        custom_prompt: 自定义提示词

    Returns:
        完整的提示词
    """
    if mode == 'custom' and custom_prompt:
        # 自定义模式，将内容插入到用户提示词中
        return f"{custom_prompt}\n\n标签: {tag_name}\n\n笔记内容：\n{content}"
    else:
        # 预设模式
        template = REFINEMENT_MODES.get(mode, {}).get('prompt_template', '')
        if template:
            return template.format(content=content)
        else:
            return f"请对以下内容进行提炼分析：\n\n{content}"


def refine_tag_content(tag_name: str, mode: str, config: dict, custom_prompt: str = None) -> tuple:
    """
    主提炼函数

    Args:
        tag_name: 标签名
        mode: 提炼模式
        config: 配置字典
        custom_prompt: 自定义提示词

    Returns:
        (success, result_or_error, memo_count)
    """
    # 验证配置
    is_valid, error = validate_llm_config(config)
    if not is_valid:
        return False, error, 0

    # 获取笔记
    memos = get_tag_memos(tag_name, get_db_path())
    if not memos:
        return False, "该标签下没有笔记", 0

    # 准备内容
    content = prepare_content_for_refinement(memos)
    if not content.strip():
        return False, "笔记内容为空", len(memos)

    # 构建提示词
    prompt = build_refinement_prompt(content, mode, tag_name, custom_prompt)

    # 调用 API
    success, result = call_llm_api(prompt, config)

    return success, result, len(memos)


def save_refinement_to_md(result: str, tag_name: str, mode: str, export_path: str) -> str:
    """
    导出提炼结果到 Markdown 文件

    Args:
        result: 提炼结果
        tag_name: 标签名
        mode: 提炼模式
        export_path: 导出路径

    Returns:
        文件路径
    """
    mode_name = REFINEMENT_MODES.get(mode, {}).get('name', mode)

    # 创建提炼结果目录
    refinement_dir = Path(export_path) / "refinements"
    refinement_dir.mkdir(parents=True, exist_ok=True)

    # 生成文件名
    safe_tag = sanitize_filename(tag_name)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{safe_tag}_{mode}_{timestamp}.md"
    file_path = refinement_dir / filename

    # 写入文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(f"# {tag_name} - {mode_name}\n\n")
        f.write(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"> 提炼类型：{mode_name}\n\n")
        f.write("---\n\n")
        f.write(result)

    return str(file_path)


# ============== 页面组件 ==============
def render_header():
    """渲染页面头部"""
    st.markdown('<h1 class="main-header">📝 RNViewer</h1>', unsafe_allow_html=True)
    st.markdown('<p style="color: #64748B; margin-bottom: 2rem;"></p>', unsafe_allow_html=True)


def render_stats():
    """渲染统计信息"""
    info = load_stats()

    # 检查数据库是否存在
    if info is None:
        exists, db_path = check_db_exists()
        st.warning("⚠️ 请先设置数据源")
        st.markdown("""
        <div style="text-align: center; padding: 2rem; background: #FEF3C7; border-radius: 12px; margin: 1rem 0;">
            <div style="font-size: 3rem; margin-bottom: 1rem;">📂</div>
            <p style="font-size: 1.1rem; color: #92400E; margin-bottom: 0.5rem;">数据库文件不存在</p>
            <p style="font-size: 0.875rem; color: #B45309;">请前往「设置」→「数据源」配置数据库路径</p>
        </div>
        """, unsafe_allow_html=True)
        if st.button("⚙️ 前往设置", type="primary"):
            st.session_state.current_view = 'settings'
            st.rerun()
        return

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown(f"""
        <div class="stat-card">
            <div class="stat-number">{info['memo_count']}</div>
            <div class="stat-label">笔记总数</div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown(f"""
        <div class="stat-card">
            <div class="stat-number">{info['tag_count']}</div>
            <div class="stat-label">标签数量</div>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        st.markdown(f"""
        <div class="stat-card">
            <div class="stat-number">{info['pinned_count']}</div>
            <div class="stat-label">置顶笔记</div>
        </div>
        """, unsafe_allow_html=True)

    with col4:
        st.markdown(f"""
        <div class="stat-card">
            <div class="stat-number">{info['untagged_count']}</div>
            <div class="stat-label">无标签笔记</div>
        </div>
        """, unsafe_allow_html=True)


def render_search():
    """渲染搜索区域"""
    st.markdown("### 🔍 搜索笔记")

    keyword = st.text_input(
        "输入关键词搜索",
        placeholder="搜索标题或内容...",
        key="search_input",
        label_visibility="collapsed"
    )

    if keyword:
        st.session_state.search_keyword = keyword
        memos = search_memos(keyword)

        if memos:
            st.markdown(f"找到 **{len(memos)}** 条结果")
            render_memo_list(memos)
        else:
            st.markdown("""
            <div class="empty-state">
                <div class="empty-state-icon">🔍</div>
                <p>未找到匹配的笔记</p>
                <p style="font-size: 0.875rem;">尝试使用其他关键词</p>
            </div>
            """, unsafe_allow_html=True)


def render_tags_sidebar():
    """渲染侧边栏"""
    st.sidebar.markdown("### 📝 RNViewer")

    if st.sidebar.button("🏠 全部笔记", use_container_width=True):
        st.session_state.current_view = 'home'
        st.session_state.selected_tag = None
        st.rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("✨ 内容提炼", use_container_width=True):
        st.session_state.current_view = 'refinement'
        st.rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("⚙️ 设置", use_container_width=True):
        st.session_state.current_view = 'settings'
        st.rerun()


def render_tags_list():
    """渲染标签列表页面"""
    st.markdown("### 📁 按标签浏览")

    # 获取统计信息以显示无标签笔记数量
    info = load_stats()

    # 检查数据库是否存在
    if info is None:
        st.info("💡 请先设置数据源以浏览标签")
        return

    untagged_count = info.get('untagged_count', 0)

    tags = load_tags()

    # 过滤掉笔记数为0的标签
    tags = [tag for tag in tags if tag.note_count > 0]

    if not tags and untagged_count == 0:
        st.info("暂无标签")
        return

    # 使用网格布局
    cols_per_row = 3

    # 添加"无标签笔记"按钮（如果有无标签笔记）
    if untagged_count > 0:
        st.warning(f"⚠️ 有 {untagged_count} 条笔记未设置标签，建议整理")
        if st.button("查看无标签笔记", key="tag_btn_untagged", use_container_width=True):
            st.session_state.current_view = 'tag'
            st.session_state.selected_tag = '__untagged__'
            st.rerun()

    for i in range(0, len(tags), cols_per_row):
        cols = st.columns(cols_per_row)
        for j, col in enumerate(cols):
            if i + j < len(tags):
                tag = tags[i + j]
                with col:
                    if st.button(
                        f"📌 {tag.name}\n{tag.note_count} 条笔记",
                        key=f"tag_btn_{tag.id}",
                        use_container_width=True
                    ):
                        st.session_state.current_view = 'tag'
                        st.session_state.selected_tag = tag.name
                        st.rerun()


def render_memo_list(memos: list):
    """渲染笔记列表"""
    for memo in memos:
        # 使用 container 创建卡片效果
        with st.container(border=True):
            col1, col2 = st.columns([4, 1])

            with col1:
                # 标题
                title = memo.title or "无标题"
                st.markdown(f"**{title}**")

                # 预览内容
                if memo.md_content:
                    preview = memo.md_content[:150]

                    # 将图片链接替换为"……"
                    preview = re.sub(r'!\[[^\]]*\]\([^)]+\)', '……', preview)

                    # 转换内部链接为可点击的查询参数格式
                    preview = convert_internal_links_for_st(preview, memo.id)
                    # 直接渲染 markdown
                    st.markdown(preview)
                    if len(memo.md_content) > 150:
                        st.markdown("...")

            with col2:
                if st.button("查看", key=f"view_{memo.id}", use_container_width=True):
                    save_source_view()  # 保存来源视图
                    st.session_state.current_view = 'detail'
                    st.session_state.selected_memo_id = memo.id
                    st.rerun()


def sanitize_filename(name: str) -> str:
    """清理文件名，移除不允许的字符"""
    if not name:
        return "note"
    # 移除 Windows 不允许的字符
    invalid_chars = r'<>:"/\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    # 移除首尾空格和点
    name = name.strip('. ')
    return name or "note"


def render_memo_detail():
    """渲染笔记详情"""
    memo_id = st.session_state.selected_memo_id

    # 检查数据库是否存在
    exists, db_path = check_db_exists()
    if not exists:
        st.warning("⚠️ 请先设置数据源")
        if st.button("⚙️ 前往设置", type="primary"):
            st.session_state.current_view = 'settings'
            st.rerun()
        return

    memo = load_memo_detail(memo_id)

    if not memo:
        st.error("笔记不存在")
        return

    # 标题
    st.markdown(f"# {memo.title or '无标题'}")

    # 元信息
    meta_parts = []
    if memo.create_datetime:
        meta_parts.append(f"创建时间: {memo.create_datetime.strftime('%Y-%m-%d %H:%M')}")
    if memo.tags:
        meta_parts.append(f"标签: {', '.join(memo.tags)}")

    if meta_parts:
        st.markdown(f"<p style='color: #64748B;'>{' | '.join(meta_parts)}</p>", unsafe_allow_html=True)

    st.markdown("---")

    # 内容（Markdown 渲染）
    if memo.md_content:
        # 转换 reminds:// 图片协议为 base64
        converted_content = convert_reminds_images(memo.md_content)
        # 将内部链接转换为带有查询参数的链接，传入当前笔记ID
        converted_content = convert_internal_links_for_st(converted_content, memo.id)

        # 使用 st.markdown 直接渲染
        st.markdown(converted_content, unsafe_allow_html=True)
    else:
        st.info("此笔记暂无内容")

    # 提取并显示关联笔记（移到内容下方）
    internal_links = extract_internal_links(memo.md_content)
    if internal_links:
        st.markdown("---")
        st.markdown("### 🔗 关联笔记")
        for link_text, uuid, title in internal_links:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"📄 {title}")
            with col2:
                if st.button("打开", key=f"link_{uuid}_{memo.id}"):
                    linked_memo = get_memo_by_uuid(uuid, get_db_path())
                    if linked_memo:
                        # 将当前笔记加入历史栈
                        st.session_state.memo_history.append(memo.id)
                        st.session_state.selected_memo_id = linked_memo.id
                        st.rerun()
                    else:
                        st.error("笔记不存在")

    # 导出按钮
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 1, 2])

    with col1:
        if st.button("📋 复制内容", use_container_width=True):
            if memo.md_content:
                st.code(memo.md_content, language="markdown")
                st.success("内容已显示在上方代码块中，可复制")

    with col2:
        # 导出为文件
        if st.button("💾 导出 MD", use_container_width=True):
            safe_title = sanitize_filename(memo.title)
            export_path = Path(st.session_state.config.get('export_path', 'docs')) / f"{safe_title}_{memo.id}.md"
            export_path.parent.mkdir(parents=True, exist_ok=True)

            # 处理图片：复制到 images 目录并更新路径
            processed_content = process_export_images(memo.md_content or "", export_path)

            with open(export_path, 'w', encoding='utf-8') as f:
                f.write(f"# {memo.title or '无标题'}\n\n")
                if memo.tags:
                    f.write(f"标签: {', '.join(memo.tags)}\n\n")
                f.write(processed_content)

            st.success(f"已导出到: {export_path}")

    # 返回按钮 - 放在底部
    st.markdown("---")
    history = st.session_state.memo_history
    source_view = st.session_state.source_view
    source_tag = st.session_state.source_tag

    if history:
        # 有历史记录，显示返回上一笔记按钮
        col_back1, col_back2 = st.columns([1, 1])
        with col_back1:
            if st.button(f"← 返回上一笔记", use_container_width=True):
                prev_memo_id = history.pop()
                st.session_state.selected_memo_id = prev_memo_id
                st.rerun()
        with col_back2:
            return_label = "返回标签列表" if source_view == 'tag' else "返回首页"
            if st.button(f"← {return_label}", use_container_width=True):
                st.session_state.current_view = source_view
                st.session_state.selected_tag = source_tag
                st.session_state.selected_memo_id = None
                st.session_state.memo_history = []  # 清空历史
                st.rerun()
    else:
        return_label = "返回标签列表" if source_view == 'tag' else "返回首页"
        if st.button(f"← {return_label}", use_container_width=True):
            st.session_state.current_view = source_view
            st.session_state.selected_tag = source_tag
            st.session_state.selected_memo_id = None
            st.rerun()


def export_memos_to_md(memos: list, title: str, export_path: Path) -> int:
    """
    导出笔记列表到 Markdown 文件

    Args:
        memos: 笔记列表
        title: 文档标题
        export_path: 输出文件路径

    Returns:
        导出的笔记数量
    """
    export_path.parent.mkdir(parents=True, exist_ok=True)

    with open(export_path, 'w', encoding='utf-8') as f:
        f.write(f"# {title} 笔记汇总\n\n")
        f.write(f"> 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"> 笔记数量：{len(memos)}\n\n")
        f.write("---\n\n")

        for memo in memos:
            f.write(f"## {memo.title or '无标题'}\n\n")
            if memo.md_content:
                processed_content = process_export_images(memo.md_content, export_path)
                f.write(processed_content)
            f.write("\n\n---\n\n")

    return len(memos)


def render_tag_view():
    """渲染标签视图"""
    tag_name = st.session_state.selected_tag
    is_untagged = tag_name == '__untagged__'

    # 返回按钮
    if st.button("← 返回首页"):
        st.session_state.current_view = 'home'
        st.session_state.selected_tag = None
        st.rerun()

    # 检查数据库是否存在
    exists, db_path = check_db_exists()
    if not exists:
        st.warning("⚠️ 请先设置数据源")
        if st.button("⚙️ 前往设置", type="primary"):
            st.session_state.current_view = 'settings'
            st.rerun()
        return

    if is_untagged:
        st.markdown("### 📁 无标签笔记")
        memos = load_untagged_memos()
    else:
        st.markdown(f"### 📁 标签: {tag_name}")
        memos = load_memos_by_tag(tag_name)

    if memos:
        st.markdown(f"共 **{len(memos)}** 条笔记")
        st.markdown("---")
        render_memo_list(memos)
    else:
        if is_untagged:
            st.info("暂无无标签笔记")
        else:
            st.info("该标签下暂无笔记")

    # 导出功能
    st.markdown("---")
    export_label = "无标签笔记" if is_untagged else tag_name
    if st.button(f"💾 导出 '{export_label}' 下的所有笔记"):
        safe_name = sanitize_filename(export_label)
        export_path = Path(st.session_state.config.get('export_path', 'docs')) / f"{safe_name}_notes.md"
        count = export_memos_to_md(memos, export_label, export_path)
        st.success(f"已导出 {count} 条笔记到: {export_path}")


def render_export_page():
    """渲染导出页面"""
    st.markdown("### 💾 导出笔记")

    # 检查数据库是否存在
    exists, db_path = check_db_exists()
    if not exists:
        st.warning("⚠️ 请先设置数据源")
        if st.button("⚙️ 前往设置", type="primary"):
            st.session_state.current_view = 'settings'
            st.rerun()
        return

    st.markdown("#### 按标签导出")
    tags = load_tags()

    col1, col2 = st.columns(2)

    with col1:
        selected_tag = st.selectbox(
            "选择标签",
            options=[t.name for t in tags],
            key="export_tag_select"
        )

    with col2:
        output_name = st.text_input(
            "输出文件名",
            value=f"{selected_tag}_notes.md" if selected_tag else "export.md"
        )

    if st.button("导出选中标签", type="primary"):
        if selected_tag:
            safe_output_name = sanitize_filename(output_name)
            if not safe_output_name.endswith('.md'):
                safe_output_name += '.md'
            export_path = Path(st.session_state.config.get('export_path', 'docs')) / safe_output_name

            memos = load_memos_by_tag(selected_tag)
            count = export_memos_to_md(memos, selected_tag, export_path)
            st.success(f"已导出 {count} 条笔记到: {export_path}")
        else:
            st.warning("请先选择一个标签")


def render_refinement_page():
    """渲染内容提炼页面"""
    st.markdown("### ✨ 内容提炼")
    st.caption("使用 AI 大模型对指定标签下的笔记进行智能提炼")

    # 返回按钮
    if st.button("← 返回首页"):
        st.session_state.current_view = 'home'
        st.rerun()

    st.markdown("---")

    config = st.session_state.config

    # 检查 API 配置
    llm_config = config.get('llm', {})
    if not llm_config.get('api_key'):
        st.warning("⚠️ 请先在「设置 → 模型设置」中配置 API")
        return

    # 检查数据库是否存在
    exists, db_path = check_db_exists()
    if not exists:
        st.warning("⚠️ 请先设置数据源")
        if st.button("⚙️ 前往设置", type="primary"):
            st.session_state.current_view = 'settings'
            st.rerun()
        return

    # 标签选择
    tags = load_tags()
    tags = [t for t in tags if t.note_count > 0]  # 过滤空标签

    if not tags:
        st.info("暂无可用的标签")
        return

    col1, col2 = st.columns([3, 1])

    with col1:
        selected_tag = st.selectbox(
            "选择标签",
            options=[t.name for t in tags],
            key="refinement_tag_select"
        )

    with col2:
        # 显示该标签下的笔记数量
        tag_obj = next((t for t in tags if t.name == selected_tag), None)
        if tag_obj:
            st.markdown("<br>", unsafe_allow_html=True)
            st.caption(f"📝 {tag_obj.note_count} 条笔记")

    # 提炼模式选择
    st.markdown("#### 提炼类型")
    mode_options = {
        'summary': '📊 综合总结',
        'keypoints': '📋 提取要点',
        'outline': '📑 生成大纲',
        'custom': '✏️ 自定义提示词'
    }

    selected_mode = st.radio(
        "选择提炼类型",
        options=list(mode_options.keys()),
        format_func=lambda x: mode_options[x],
        horizontal=True,
        key="refinement_mode"
    )

    # 自定义提示词输入
    custom_prompt = None
    if selected_mode == 'custom':
        custom_prompt = st.text_area(
            "输入自定义提示词",
            placeholder="请输入你的提炼要求，例如：请提取所有涉及技术架构的内容...",
            height=100,
            key="custom_prompt_input"
        )

    # 开始提炼按钮
    st.markdown("---")

    if st.button("🔄 开始提炼", type="primary", use_container_width=True):
        if selected_mode == 'custom' and not custom_prompt:
            st.error("请输入自定义提示词")
        else:
            # 初始化结果存储
            if 'refinement_result' not in st.session_state:
                st.session_state.refinement_result = None
            if 'refinement_tag' not in st.session_state:
                st.session_state.refinement_tag = None
            if 'refinement_mode_used' not in st.session_state:
                st.session_state.refinement_mode_used = None
            if 'refinement_memo_count' not in st.session_state:
                st.session_state.refinement_memo_count = 0

            with st.spinner("正在提炼内容，请稍候..."):
                success, result, memo_count = refine_tag_content(
                    selected_tag,
                    selected_mode,
                    config,
                    custom_prompt
                )

            if success:
                st.session_state.refinement_result = result
                st.session_state.refinement_tag = selected_tag
                st.session_state.refinement_mode_used = selected_mode
                st.session_state.refinement_memo_count = memo_count
                st.success(f"✅ 提炼完成！共处理 {memo_count} 条笔记")
            else:
                st.error(f"❌ 提炼失败: {result}")

    # 显示结果
    if st.session_state.get('refinement_result'):
        st.markdown("---")
        st.markdown("#### 提炼结果")

        # 显示结果信息
        result_tag = st.session_state.get('refinement_tag', '')
        result_mode = st.session_state.get('refinement_mode_used', '')
        result_count = st.session_state.get('refinement_memo_count', 0)
        mode_name = REFINEMENT_MODES.get(result_mode, {}).get('name', result_mode)

        st.caption(f"标签: {result_tag} | 类型: {mode_name} | 笔记数: {result_count}")

        # 渲染结果
        st.markdown(st.session_state.refinement_result)

        # 导出按钮
        st.markdown("---")
        col_export1, col_export2 = st.columns(2)

        with col_export1:
            if st.button("📋 复制结果", use_container_width=True):
                st.code(st.session_state.refinement_result, language="markdown")
                st.success("内容已显示在上方代码块中，可复制")

        with col_export2:
            if st.button("💾 导出 MD", use_container_width=True):
                file_path = save_refinement_to_md(
                    st.session_state.refinement_result,
                    result_tag,
                    result_mode,
                    config.get('export_path', 'docs')
                )
                st.success(f"已导出到: {file_path}")


def render_settings_page():
    """渲染设置页面"""
    st.markdown("### ⚙️ 设置")

    config = st.session_state.config

    # 返回按钮
    if st.button("← 返回首页"):
        st.session_state.current_view = 'home'
        st.rerun()

    st.markdown("---")

    # 创建标签页
    tab1, tab2, tab3 = st.tabs(["📦 数据源", "🤖 模型设置", "📁 导出路径"])

    # ===== 数据源设置 =====
    with tab1:
        st.markdown("#### 数据库配置")

        # 初始化 session_state 中的数据库路径
        if 'db_path_value' not in st.session_state:
            db_path_config = config.get('db_path', '')
            # 如果配置的路径存在则使用，否则显示为空（让 placeholder 生效）
            if db_path_config and Path(db_path_config).exists():
                st.session_state.db_path_value = db_path_config
            else:
                st.session_state.db_path_value = ''

        # 浏览按钮回调函数
        def browse_db_file():
            try:
                import tkinter as tk
                from tkinter import filedialog

                # 创建隐藏的根窗口
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)

                # 打开文件选择对话框
                initial_dir = str(Path.home())
                if st.session_state.db_path_value:
                    parent_dir = Path(st.session_state.db_path_value).parent
                    if parent_dir.exists():
                        initial_dir = str(parent_dir)

                selected_file = filedialog.askopenfilename(
                    title="选择 Reminds 数据库文件",
                    initialdir=initial_dir,
                    filetypes=[("SQLite 数据库", "*.db"), ("所有文件", "*.*")]
                )

                root.destroy()

                if selected_file:
                    st.session_state.db_path_value = selected_file
            except Exception as e:
                st.session_state.browse_db_error = str(e)

        # 使用两列布局：输入框 + 浏览按钮
        col_db1, col_db2 = st.columns([4, 1])
        with col_db1:
            db_path = st.text_input(
                "数据库路径",
                help="Reminds 数据库文件位置",
                placeholder="reminds.db",
                key="db_path_value"
            )
        with col_db2:
            st.markdown("<br>", unsafe_allow_html=True)  # 对齐按钮
            if st.button("📂 浏览", key="browse_db_file", on_click=browse_db_file):
                st.rerun()

        # 显示浏览错误（如果有）
        if 'browse_db_error' in st.session_state:
            st.error(f"无法打开文件选择器: {st.session_state.browse_db_error}")
            st.info("💡 请手动输入路径，或确保已安装 tkinter")
            del st.session_state.browse_db_error

        # 显示数据库状态
        db_file = Path(st.session_state.db_path_value)
        if db_file.exists():
            st.success(f"✅ 数据库文件存在")
            st.caption(f"路径: {st.session_state.db_path_value}")

            # 显示数据库大小和修改时间
            db_size = db_file.stat().st_size / 1024 / 1024  # MB
            db_mtime = datetime.fromtimestamp(db_file.stat().st_mtime)
            st.caption(f"大小: {db_size:.2f} MB | 修改时间: {db_mtime.strftime('%Y-%m-%d %H:%M')}")
        else:
            st.error(f"❌ 数据库文件不存在")
            st.caption(f"路径: {st.session_state.db_path_value}")

    # ===== 模型设置 =====
    with tab2:
        st.markdown("#### 大模型 API 配置")
        st.caption("配置用于笔记总结和智能分析的大模型 API（兼容 OpenAI 格式）")

        llm_config = config.get('llm', DEFAULT_CONFIG['llm'])

        base_url = st.text_input(
            "Base URL",
            value=llm_config.get('base_url', ''),
            placeholder="https://api.openai.com/v1",
            help="API 基础地址，如 https://api.openai.com/v1 或其他兼容服务地址"
        )

        api_key = st.text_input(
            "API Key",
            value=llm_config.get('api_key', ''),
            type="password",
            placeholder="sk-...",
            help="API 密钥，已配置的密钥将显示为星号"
        )

        model_name = st.text_input(
            "模型名称",
            value=llm_config.get('model_name', 'gpt-4o-mini'),
            placeholder="gpt-4o-mini, gpt-4, claude-3-sonnet 等",
            help="要使用的模型名称"
        )

        # 显示配置状态
        if api_key:
            st.success("✅ API Key 已配置")
        else:
            st.warning("⚠️ API Key 未配置，AI 功能将不可用")

        # 测试连接按钮
        if st.button("🔗 测试连接", type="secondary"):
            if not api_key:
                st.error("请先配置 API Key")
            elif not base_url:
                st.error("请先配置 Base URL")
            else:
                with st.spinner("正在测试连接..."):
                    try:
                        import requests
                        headers = {
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json"
                        }

                        # 构建 chat completions 端点
                        url = build_llm_url(base_url)

                        # 发送简单测试请求
                        test_payload = {
                            "model": model_name,
                            "messages": [{"role": "user", "content": "Hi"}],
                            "max_tokens": 5
                        }

                        response = requests.post(
                            url,
                            headers=headers,
                            json=test_payload,
                            timeout=30
                        )

                        if response.status_code == 200:
                            st.success("✅ 连接成功！API 响应正常")
                            result = response.json()
                            if 'choices' in result:
                                st.caption(f"模型响应: {result['choices'][0].get('message', {}).get('content', 'N/A')[:50]}")
                        else:
                            error_msg = response.text[:200]
                            if response.status_code == 404:
                                st.error(f"❌ 端点不存在 (404)。请检查 Base URL 格式")
                                st.caption(f"尝试的 URL: {url}")
                                st.caption(f"建议格式: https://api.example.com/v1")
                            elif response.status_code == 401:
                                st.error("❌ 认证失败。请检查 API Key 是否正确")
                            else:
                                st.error(f"❌ 连接失败: {response.status_code}")
                                st.caption(f"错误信息: {error_msg}")
                    except requests.exceptions.Timeout:
                        st.error("❌ 连接超时，请检查网络或稍后重试")
                    except requests.exceptions.ConnectionError:
                        st.error("❌ 无法连接到服务器，请检查 Base URL 是否正确")
                    except Exception as e:
                        st.error(f"❌ 连接失败: {str(e)}")

    # ===== 导出路径设置 =====
    with tab3:
        st.markdown("#### 导出路径配置")

        # 初始化 session_state 中的导出路径
        if 'export_path_value' not in st.session_state:
            st.session_state.export_path_value = config.get('export_path', DEFAULT_CONFIG['export_path'])

        # 浏览按钮回调函数
        def browse_directory():
            try:
                import tkinter as tk
                from tkinter import filedialog

                # 创建隐藏的根窗口
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)

                # 打开目录选择对话框
                selected_dir = filedialog.askdirectory(
                    title="选择导出目录",
                    initialdir=st.session_state.export_path_value if Path(st.session_state.export_path_value).exists() else str(Path.home())
                )

                root.destroy()

                if selected_dir:
                    st.session_state.export_path_value = selected_dir
            except Exception as e:
                st.session_state.browse_error = str(e)

        # 使用两列布局：输入框 + 浏览按钮
        col_path1, col_path2 = st.columns([4, 1])
        with col_path1:
            export_path = st.text_input(
                "默认导出目录",
                help="笔记导出的默认保存目录",
                key="export_path_value"
            )
        with col_path2:
            st.markdown("<br>", unsafe_allow_html=True)  # 对齐按钮
            if st.button("📁 浏览", key="browse_export_dir", on_click=browse_directory):
                st.rerun()

        # 显示浏览错误（如果有）
        if 'browse_error' in st.session_state:
            st.error(f"无法打开目录选择器: {st.session_state.browse_error}")
            st.info("💡 请手动输入路径，或确保已安装 tkinter")
            del st.session_state.browse_error

        # 显示导出目录状态
        export_dir = Path(export_path)
        if export_dir.exists():
            st.success(f"✅ 目录存在")
            st.caption(f"路径: {export_path}")

            # 显示目录中的文件数量
            try:
                md_files = list(export_dir.glob("*.md"))
                st.caption(f"已导出文件: {len(md_files)} 个 Markdown 文件")
            except Exception:
                pass
        else:
            st.warning(f"⚠️ 目录不存在，将在导出时自动创建")
            st.caption(f"路径: {export_path}")

    st.markdown("---")

    # 保存按钮
    col1, col2, col3 = st.columns([1, 1, 2])

    with col1:
        if st.button("💾 保存设置", type="primary", use_container_width=True):
            # 更新配置
            new_config = {
                "db_path": st.session_state.db_path_value,
                "export_path": st.session_state.export_path_value,
                "llm": {
                    "base_url": base_url,
                    "api_key": api_key,
                    "model_name": model_name
                }
            }

            if save_config(new_config):
                st.session_state.config = new_config
                # 删除缓存的值，让页面刷新后重新从配置读取
                if 'db_path_value' in st.session_state:
                    del st.session_state.db_path_value
                if 'export_path_value' in st.session_state:
                    del st.session_state.export_path_value
                st.success("✅ 设置已保存")
                st.rerun()
            else:
                st.error("❌ 保存失败")

    with col2:
        if st.button("🔄 重置默认", use_container_width=True):
            st.session_state.config = DEFAULT_CONFIG.copy()
            save_config(DEFAULT_CONFIG)
            # 删除缓存的值，让页面刷新后重新从配置读取
            if 'db_path_value' in st.session_state:
                del st.session_state.db_path_value
            if 'export_path_value' in st.session_state:
                del st.session_state.export_path_value
            st.success("已重置为默认设置")
            st.rerun()


# ============== 主应用 ==============
def check_url_navigation():
    """检测URL参数或路径，处理内部链接跳转"""
    uuid = None
    from_memo_id = None

    # 方式1：检查查询参数 ?goto_uuid=xxx
    params = st.query_params
    if 'goto_uuid' in params:
        uuid = params['goto_uuid']
        from_memo_id = params.get('from_memo')

    # 方式2：检查路径 /memo/uuid（通过 JavaScript 获取）
    # 由于 Streamlit 不直接暴露路径，我们使用 components.html 来处理

    if uuid:
        memo = get_memo_by_uuid(uuid, get_db_path())
        if memo:
            # 只有当前不在笔记详情页时才保存来源视图
            if st.session_state.current_view != 'detail':
                save_source_view()

            # 处理来源笔记ID
            if from_memo_id:
                try:
                    from_memo_id = int(from_memo_id)
                    st.session_state.memo_history.append(from_memo_id)
                except (ValueError, TypeError):
                    pass
            else:
                current_memo_id = st.session_state.selected_memo_id
                if current_memo_id:
                    st.session_state.memo_history.append(current_memo_id)

            # 跳转到目标笔记
            st.session_state.selected_memo_id = memo.id
            st.session_state.current_view = 'detail'
            st.query_params.clear()
            st.rerun()


def handle_path_navigation():
    """
    处理 /memo/uuid 格式的路径导航

    使用 JavaScript 检测 URL 并重定向到查询参数格式
    """
    js_code = '''
    <script>
    // 检查当前 URL 是否是 /memo/uuid 格式
    var path = window.location.pathname;
    var match = path.match(/^\\/memo\\/([a-f0-9-]+)$/);
    if (match) {
        var uuid = match[1];
        // 重定向到查询参数格式
        window.location.href = window.location.origin + '/?goto_uuid=' + uuid;
    }
    </script>
    '''
    components.html(js_code, height=0)


def main():
    """主函数"""
    init_session_state()
    handle_path_navigation()  # 处理 /memo/uuid 路径格式
    check_url_navigation()  # 检测URL跳转参数

    # 页面顶部锚点
    st.markdown('<div id="page-top"></div>', unsafe_allow_html=True)

    # 侧边栏
    render_tags_sidebar()

    # 主内容区
    render_header()

    # 根据当前视图渲染不同内容
    current_view = st.session_state.current_view

    if current_view == 'home':
        # 统计信息
        render_stats()
        st.markdown("---")

        # 搜索
        render_search()
        st.markdown("---")

        # 标签浏览
        render_tags_list()

    elif current_view == 'tag':
        render_tag_view()

    elif current_view == 'detail':
        render_memo_detail()

    elif current_view == 'refinement':
        render_refinement_page()

    elif current_view == 'settings':
        render_settings_page()

    # 页面底部锚点
    st.markdown('<div id="page-bottom"></div>', unsafe_allow_html=True)

    # 浮动跳转按钮（固定在右下角）
    st.markdown('''
    <style>
        .float-nav {
            position: fixed;
            right: 20px;
            bottom: 80px;
            display: flex;
            flex-direction: column;
            gap: 10px;
            z-index: 9999;
        }
        .float-nav a {
            width: 44px;
            height: 44px;
            background-color: #4F46E5;
            color: white;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            text-decoration: none;
            font-size: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            transition: all 0.2s;
        }
        .float-nav a:hover {
            background-color: #4338CA;
            transform: scale(1.1);
        }
    </style>
    <div class="float-nav">
        <a href="#page-top" title="跳到顶部">⬆️</a>
        <a href="#page-bottom" title="跳到底部">⬇️</a>
    </div>
    ''', unsafe_allow_html=True)

    # 页脚
    st.markdown("---")
    st.markdown(
        "<p style='text-align: center; color: #94A3B8; font-size: 0.875rem;'>"
        "RNViewer - RN知识库浏览工具 | 开发者：De-hamster"
        "</p>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()