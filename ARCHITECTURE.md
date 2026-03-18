# RNViewer 程序实现原理说明

> 本文档详细说明 RNViewer 的架构设计、模块实现和技术细节，供后续开发参考。

---

## 1. 整体架构

### 1.1 技术栈

| 组件 | 技术 | 版本要求 |
|------|------|----------|
| Web 框架 | Streamlit | >= 1.30.0 |
| HTTP 客户端 | Requests | >= 2.28.0 |
| 数据库 | SQLite3 | Python 内置 |
| 数据处理 | dataclasses | Python 内置 |

### 1.2 目录结构

```
RNViewer/
├── Rnv.py                 # 主程序（UI + 业务逻辑）
└── lib/
    ├── __init__.py        # 模块初始化，导出便捷函数
    └── reminds.py         # 数据库操作封装
```

### 1.3 架构图

```
┌─────────────────────────────────────────────────────────┐
│                    Streamlit Web UI                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ 首页视图  │ │ 标签视图  │ │ 详情视图  │ │ 设置视图  │    │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘    │
│       │            │            │            │          │
│       └────────────┴────────────┴────────────┘          │
│                         │                                │
│              ┌──────────▼──────────┐                    │
│              │   Session State     │                    │
│              │  (会话状态管理)      │                    │
│              └──────────┬──────────┘                    │
└─────────────────────────┼───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                    lib/reminds.py                        │
│  ┌─────────────────────────────────────────────────┐    │
│  │ RemindsDB - 数据库操作类                          │    │
│  │  - search_memos() 搜索笔记                        │    │
│  │  - get_memos_by_tag() 按标签查询                  │    │
│  │  - get_memo() 获取笔记详情                        │    │
│  └─────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────┐    │
│  │ 便捷函数 (全局函数)                               │    │
│  │  - search(), get_tag_memos(), list_tags()...     │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│              SQLite Database (reminds.db)                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                │
│  │   memo   │ │   tag    │ │memo_tag   │                │
│  │  笔记表   │ │  标签表   │ │关联表     │                │
│  └──────────┘ └──────────┘ └──────────┘                │
└─────────────────────────────────────────────────────────┘
```

---

## 2. 核心模块说明

### 2.1 Rnv.py - 主程序

主程序负责 UI 渲染和业务逻辑处理。

#### 2.1.1 配置管理

```python
CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "db_path": str(Path(os.environ.get('APPDATA', '')) / 'reminds' / 'reminds.db'),
    "export_path": "docs",
    "llm": {"base_url": "", "api_key": "", "model_name": "gpt-4o-mini"}
}
```

配置保存在 `config.json` 文件中，包含：
- `db_path`: Reminds 数据库路径
- `export_path`: 导出文件默认目录
- `llm`: 大模型 API 配置

#### 2.1.2 会话状态管理

使用 Streamlit 的 `st.session_state` 管理应用状态：

| 状态变量 | 类型 | 说明 |
|----------|------|------|
| `current_view` | str | 当前视图 (home/tag/detail/settings/refinement) |
| `selected_tag` | str | 选中的标签名 |
| `selected_memo_id` | int | 选中的笔记ID |
| `memo_history` | list | 笔记导航历史栈 |
| `config` | dict | 应用配置 |
| `refinement_result` | str | AI 提炼结果 |

#### 2.1.3 图片路径处理

Reminds 笔记中的图片使用自定义协议 `reminds://img/`，需要转换为本地路径：

```python
def get_reminds_img_path() -> Path:
    """根据数据库路径动态计算图片目录"""
    config = st.session_state.get('config', {})
    db_path = config.get('db_path', '')
    if db_path:
        db_dir = Path(db_path).parent
        return db_dir / 'statics' / 'img'
    return Path(os.environ.get('APPDATA', '')) / 'reminds' / 'statics' / 'img'
```

图片在浏览器中显示时转换为 Base64 Data URL：

```python
def convert_reminds_images(md_content: str) -> str:
    """将 reminds://img/ 协议图片转换为 base64 data URL"""
    # 匹配 ![alt](reminds://img/xxx.jpg)
    # 转换为 ![alt](data:image/jpeg;base64,...)
```

#### 2.1.4 内部链接处理

笔记间的内部链接格式为 `/memo/{uuid}`，转换为可点击的查询参数：

```python
def convert_internal_links_for_st(md_content: str, current_memo_id: int = None) -> str:
    """将 /memo/uuid 链接转换为 ?goto_uuid=xxx 格式"""
    # [文字](/memo/uuid) -> [文字](?goto_uuid=uuid&from_memo=id)
```

导航历史使用栈结构管理：

```python
# 点击内部链接时，将当前笔记加入历史栈
st.session_state.memo_history.append(current_memo_id)

# 返回时弹出历史栈
prev_memo_id = st.session_state.memo_history.pop()
```

### 2.2 lib/reminds.py - 数据库模块

封装 Reminds 数据库操作，提供简洁的查询接口。

#### 2.2.1 数据模型

```python
@dataclass
class Memo:
    """笔记数据模型"""
    id: int
    title: str
    md_content: Optional[str] = None
    uuid: Optional[str] = None
    pinned: bool = False
    tags: List[str] = field(default_factory=list)

@dataclass
class Tag:
    """标签数据模型"""
    id: int
    name: str
    note_count: int = 0
```

#### 2.2.2 数据库连接

支持上下文管理器和手动管理两种方式：

```python
# 推荐：上下文管理器
with RemindsDB() as db:
    memos = db.search_memos('关键词')

# 手动管理
db = RemindsDB('/path/to/reminds.db')
db.connect()
try:
    memos = db.get_all_memos()
finally:
    db.close()
```

#### 2.2.3 便捷函数

模块初始化时创建全局数据库连接，提供便捷函数：

```python
# lib/__init__.py
_db = RemindsDB()
_db.connect()

def search(keyword: str) -> List[Memo]:
    return _db.search_memos(keyword)

def get_tag_memos(tag_name: str) -> List[Memo]:
    return _db.get_memos_by_tag(tag_name)
```

---

## 3. 功能模块实现

### 3.1 首页视图 (render_stats + render_search + render_tags_list)

```
┌─────────────────────────────────────────────┐
│  统计卡片: 笔记总数 | 标签数量 | 置顶 | 无标签  │
├─────────────────────────────────────────────┤
│  热门标签 (可点击)                            │
├─────────────────────────────────────────────┤
│  搜索框                                      │
├─────────────────────────────────────────────┤
│  标签网格 (3列布局)                           │
│  ┌───────┐ ┌───────┐ ┌───────┐              │
│  │ 标签1 │ │ 标签2 │ │ 标签3 │              │
│  └───────┘ └───────┘ └───────┘              │
└─────────────────────────────────────────────┘
```

### 3.2 笔记详情视图 (render_memo_detail)

```
┌─────────────────────────────────────────────┐
│  # 笔记标题                                  │
│  创建时间 | 标签列表                          │
├─────────────────────────────────────────────┤
│  Markdown 内容渲染                           │
│  (支持图片、代码、内部链接)                    │
├─────────────────────────────────────────────┤
│  关联笔记列表 (如果有内部链接)                  │
├─────────────────────────────────────────────┤
│  [复制内容] [导出MD] [返回]                   │
└─────────────────────────────────────────────┘
```

### 3.3 内容提炼功能 (render_refinement_page)

```
┌─────────────────────────────────────────────┐
│  标签选择: [下拉框] (显示笔记数量)              │
├─────────────────────────────────────────────┤
│  提炼类型: ○综合总结 ○提取要点 ○生成大纲 ○自定义 │
├─────────────────────────────────────────────┤
│  [开始提炼]                                   │
├─────────────────────────────────────────────┤
│  提炼结果 (Markdown 渲染)                     │
│  [复制] [导出MD]                              │
└─────────────────────────────────────────────┘
```

提炼流程：

```python
def refine_tag_content(tag_name, mode, config, custom_prompt):
    # 1. 获取标签下所有笔记
    memos = get_tag_memos(tag_name)

    # 2. 格式化内容（限制50000字符）
    content = prepare_content_for_refinement(memos)

    # 3. 构建提示词
    prompt = build_refinement_prompt(content, mode, tag_name, custom_prompt)

    # 4. 调用 LLM API
    success, result = call_llm_api(prompt, config)

    return success, result, len(memos)
```

---

## 4. 数据库结构

Reminds 数据库表结构：

### memo 表 (笔记)
```sql
CREATE TABLE memo (
    id INTEGER PRIMARY KEY,
    title TEXT,
    md_content TEXT,
    html_content TEXT,
    create_time INTEGER,
    last_mod_time INTEGER,
    uuid TEXT,
    pinned INTEGER,
    state TEXT DEFAULT '0'
);
```

### tag 表 (标签)
```sql
CREATE TABLE tag (
    id INTEGER PRIMARY KEY,
    name TEXT,
    create_time INTEGER
);
```

### memo_tag 表 (关联)
```sql
CREATE TABLE memo_tag (
    memo_id INTEGER,
    tag_id INTEGER,
    PRIMARY KEY (memo_id, tag_id)
);
```

---

## 5. 导出功能

### 5.1 单篇笔记导出

```python
def render_memo_detail():
    # 处理图片：复制到 images 目录
    processed_content = process_export_images(memo.md_content, export_path)

    # 写入文件
    with open(export_path, 'w') as f:
        f.write(f"# {memo.title}\n\n")
        f.write(f"标签: {', '.join(memo.tags)}\n\n")
        f.write(processed_content)
```

### 5.2 标签批量导出

```python
def render_tag_view():
    for memo in memos:
        f.write(f"## {memo.title}\n\n")
        f.write(process_export_images(memo.md_content, export_path))
        f.write("\n\n---\n\n")
```

---

## 6. 样式定制

使用 CSS 注入定制 Streamlit 默认样式：

```python
st.markdown("""
<style>
    :root {
        --primary-color: #2563EB;
        --secondary-color: #3B82F6;
    }
    .note-card {
        background: white;
        border-radius: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    .tag-badge {
        background: #EFF6FF;
        color: var(--primary-color);
        border-radius: 9999px;
    }
</style>
""", unsafe_allow_html=True)
```

---

## 7. 扩展开发指南

### 7.1 添加新视图

1. 在 `render_tags_sidebar()` 中添加导航按钮
2. 创建 `render_xxx_page()` 函数
3. 在 `main()` 的视图分发逻辑中添加处理

### 7.2 添加新的提炼模式

在 `REFINEMENT_MODES` 字典中添加新模式：

```python
REFINEMENT_MODES = {
    'new_mode': {
        'name': '新模式名称',
        'prompt_template': """提示词模板..."""
    }
}
```

### 7.3 添加新的数据源

1. 在 `lib/` 下创建新的数据库操作模块
2. 在 `Rnv.py` 中添加数据加载函数
3. 在设置页面添加数据源配置

---

## 8. 已知限制

1. **图片处理**: 仅支持 `reminds://img/` 协议的本地图片
2. **内容长度**: AI 提炼限制 50000 字符
3. **并发访问**: SQLite 数据库不适合高并发写入
4. **浏览器兼容**: 主要测试 Chrome/Edge，其他浏览器可能有兼容问题

---

## 9. 性能优化建议

1. **图片缓存**: 考虑对 Base64 图片进行缓存
2. **分页加载**: 笔记数量过多时考虑分页
3. **懒加载**: 长内容笔记考虑分段加载
4. **API 超时**: 添加可配置的超时时间

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0.0 | 2026-03-17 | 初始版本发布 |

---

*文档维护: De-hamster*
*最后更新: 2026-03-17*