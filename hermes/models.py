"""The model catalog — the one place that knows what's different between the
models Hermes can drive.

Hermes was built around NousResearch Hermes-4.3-36B (FP8 safetensors on vLLM),
but nothing in the agent loop is actually tied to it: the wire protocol is
plain OpenAI chat-completions. So "supporting another model" reduces to a row
in this table — its weights, how vLLM should quantize/parse it, how much VRAM
it needs, and how far its context stretches. `gpu serve` lets the operator pick
a row; everything model-specific downstream (the tier planner, the vLLM launch
command, the system-prompt identity) reads it from here.

Two polarities on purpose:
  - `ready=True`  — battle-tested, the path the app was tuned on.
  - `ready=False` — wired but experimental (e.g. GGUF on vLLM is single-GPU and
    slower than native FP8); the picker flags it so the operator knows.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelSpec:
    key: str  # short id stored in config + used as the served model name root
    label: str  # what the picker shows
    repo: str  # HF repo id (positional vllm arg, unless GGUF)
    identity: str  # how the model is told to refer to itself in the system prompt
    min_total_gb: int  # VRAM floor — weights + runtime overhead
    max_model_len: int  # the longest context the model itself supports
    context_tiers: list  # [(total_gb_threshold, max_model_len), ...] first fit wins
    context_beyond: int  # context when VRAM exceeds every tier threshold
    weights_note: str  # shown while the box downloads ("~37GB", ...)
    served_name: str  # the `model` string the OpenAI client must send
    server: str = "vllm"  # runtime: "vllm" (FP8 safetensors) or "llama_cpp" (GGUF)
    quantization: str = "fp8"
    tool_call_parser: str = "hermes"
    gguf_file: str | None = None  # exact filename within `repo` (GGUF)
    gguf_quant: str | None = None  # or a quant tag llama.cpp resolves (repo:QUANT)
    tokenizer: str | None = None  # override tokenizer source (GGUF sometimes needs it)
    ready: bool = True
    notes_extra: list = field(default_factory=list)  # extra serve-time warnings

    # ---- per-model build profile ------------------------------------------
    # The app's loop, package, and tools were tuned around Hermes. Nothing in
    # them is Hermes-specific, but the *settings* that make tool-calling
    # reliable differ per model: how it samples, how much room its reasoning
    # needs, which reasoning tags it emits, and how much hand-holding its
    # tool-call discipline wants. These defaults ARE the Hermes baseline, so a
    # spec that sets none of them behaves exactly as the app always has.
    sampling: dict = field(
        default_factory=lambda: {"temperature": 0.6, "top_p": 0.95, "top_k": 20}
    )
    max_completion_tokens: int = 8192
    stall_nudges: int = 2  # how many prose-only turns to bounce before accepting
    think_tags: tuple = ("think", "seed:think")  # reasoning markers to strip
    tool_guidance: str = ""  # model-specific addendum appended to the system prompt
    supports_forced_tool_choice: bool = True  # vLLM yes; llama.cpp GGUF no

    @property
    def is_gguf(self) -> bool:
        return self.gguf_file is not None or self.gguf_quant is not None

    def runtime_config(self) -> dict:
        """The config keys the picker persists so the live app serves this
        model's tuned build. Identity/weights are set alongside in the picker;
        these are the inference knobs the agent loop and client read back."""
        return {
            "sampling": dict(self.sampling),
            "max_completion_tokens": self.max_completion_tokens,
            "stall_nudges": self.stall_nudges,
        }


# Hermes 4.3 supports up to 512K; FP8 36B weights are ~37GB, so 44GB is the floor.
HERMES = ModelSpec(
    key="hermes",
    label="Hermes-4.3-36B (NousResearch) · FP8",
    repo="NousResearch/Hermes-4.3-36B",
    identity="Hermes (NousResearch Hermes-4.3-36B)",
    min_total_gb=44,
    max_model_len=524288,
    context_tiers=[
        (56, 16384),
        (72, 32768),
        (96, 65536),
        (120, 131072),
        (168, 196608),
    ],
    context_beyond=262144,
    weights_note="first run downloads ~37GB of FP8 weights",
    served_name="NousResearch/Hermes-4.3-36B",
    quantization="fp8",
    tool_call_parser="hermes",
    ready=True,
)

# Qwen3.6-27B (HauhauCS balanced uncensored finetune), served from a Q5_K_P
# GGUF on its *native* runtime — llama.cpp's llama-server, not vLLM (whose GGUF
# path is experimental and slower). llama-server speaks the same OpenAI wire
# protocol, downloads the GGUF itself (`--hf-repo/--hf-file`), splits across
# GPUs, and emits OpenAI tool calls from the model's own chat template via
# `--jinja`. ~19GB of Q5 weights fits a single 24GB card.
QWEN = ModelSpec(
    key="qwen",
    label="Qwen3.6-27B (HauhauCS Balanced, uncensored) · Q5_K_P GGUF",
    repo="HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Balanced",
    identity=(
        "Qwen3.6-27B (the HauhauCS Balanced uncensored finetune), running as "
        "the mind of the Hermes agent system"
    ),
    min_total_gb=22,  # a 24GB card reports ~23GB; ~19GB of Q5 weights fit
    max_model_len=131072,
    context_tiers=[
        (28, 16384),
        (40, 32768),
        (56, 65536),
        (96, 98304),
    ],
    context_beyond=131072,
    weights_note="first run downloads the ~19GB Q5_K_P GGUF",
    served_name="qwen3.6-27b",
    server="llama_cpp",
    quantization="gguf",
    gguf_file="Qwen3.6-27B-Uncensored-HauhauCS-Balanced-Q5_K_P.gguf",
    ready=False,
    # Build profile: a Q5 community finetune. Slightly cooler sampling buys
    # tool-call discipline; min_p trims the junk tail quantization adds;
    # presence_penalty curbs the looping these finetunes are prone to. It
    # narrates instead of acting more than Hermes, so bounce prose harder.
    # llama.cpp's named tool_choice is unreliable under --jinja, so don't force.
    sampling={
        "temperature": 0.5,
        "top_p": 0.9,
        "top_k": 20,
        "min_p": 0.05,
        "presence_penalty": 0.6,
    },
    max_completion_tokens=8192,
    stall_nudges=3,
    think_tags=("think", "thinking"),
    supports_forced_tool_choice=False,
    tool_guidance=(
        "You are a community uncensored finetune driving this agent. Keep your "
        "tool-calling tight:\n"
        "- Act, don't narrate. This kind of model tends to describe what it "
        "would do and then stop. Every turn either makes a tool call or, if the "
        "task is truly finished, calls `finish_run` — never end a turn with a "
        "plan and no call.\n"
        "- One tool per step; read its result before the next call. Never paste "
        "shell commands or code as a message expecting someone to run them — run "
        "them yourself with a tool.\n"
        "- The operator y/n gates and the keep-internet-on-the-phone rule are "
        "real, enforced by trust rather than a cage. Honour them; a DENIED "
        "result means adapt your approach, not retry the same call."
    ),
    notes_extra=[
        "First serve builds llama.cpp with CUDA on the box (needs the CUDA "
        "toolkit / nvcc — use a CUDA-devel image, not runtime-only).",
        "Community uncensored finetune — sanity-check its tool-calling discipline "
        "before trusting it with host writes.",
    ],
)

# Qwen3.6-27B, Alibaba's official release: FP8 safetensors on vLLM, like Hermes.
# A 27B in FP8 is ~27GB, so it fits a 32GB card. Qwen3 emits Hermes-style tool
# calls, so vLLM's `hermes` parser is the reliable choice.
QWEN_OFFICIAL = ModelSpec(
    key="qwen-official",
    label="Qwen3.6-27B (Alibaba, official) · FP8",
    repo="Qwen/Qwen3.6-27B",
    identity="Qwen3.6-27B (Alibaba's official release), running as the mind of the Hermes agent system",
    min_total_gb=30,
    max_model_len=262144,
    context_tiers=[
        (40, 32768),
        (56, 65536),
        (80, 131072),
        (140, 196608),
    ],
    context_beyond=262144,
    weights_note="first run downloads ~27GB and quantizes to FP8 on the fly",
    served_name="qwen3.6-27b-official",
    server="vllm",
    quantization="fp8",
    tool_call_parser="hermes",
    ready=False,
    # Build profile: Qwen3 thinking-mode. Alibaba's published sampling for
    # reasoning is temp 0.6 / top_p 0.95 / top_k 20 / min_p 0 (greedy decoding
    # is explicitly discouraged); a little presence_penalty keeps long tool
    # chains from repeating. Reasoning eats tokens, so give the completion
    # budget headroom or the model spends it all thinking and never emits the
    # call. vLLM serves it, so forced tool_choice works.
    sampling={
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.5,
    },
    max_completion_tokens=12288,
    think_tags=("think",),
    tool_guidance=(
        "You are a Qwen3 reasoning model. Two habits keep your tool-calling "
        "crisp:\n"
        "- Think briefly, then act. It is tempting to deliberate at length; keep "
        "your hidden reasoning short and spend turns on tool calls, not "
        "analysis. Emit the tool call in the same turn you decide on it.\n"
        "- One concrete step per turn — call a tool, read the result, then "
        "decide the next. Tool calls are structured function calls, never fenced "
        "code or JSON in your message text."
    ),
)

# DavidAU's Qwen3.6-40B "MAX" GGUF (Claude-Opus-flavoured, uncensored, thinking).
# A 40B at Q5_K_M is ~28GB, so it wants a 32GB+ card or two GPUs. The repo holds
# many quant files, so we let llama.cpp resolve the file by quant tag
# (`-hf repo:Q5_K_M`); override `gguf_quant`/`gguf_file` via config to change it.
QWEN_40B = ModelSpec(
    key="qwen-40b",
    label="Qwen3.6-40B (DavidAU, Opus-Deckard Heretic, uncensored) · Q5_K_M GGUF",
    repo="DavidAU/Qwen3.6-40B-Claude-4.6-Opus-Deckard-Heretic-Uncensored-Thinking-NEO-CODE-Di-IMatrix-MAX-GGUF",
    identity=(
        "Qwen3.6-40B (DavidAU's Opus-Deckard Heretic uncensored finetune), "
        "running as the mind of the Hermes agent system"
    ),
    min_total_gb=30,
    max_model_len=131072,
    context_tiers=[
        (36, 16384),
        (48, 32768),
        (72, 65536),
        (120, 98304),
    ],
    context_beyond=131072,
    weights_note="first run downloads the ~28GB Q5_K_M GGUF",
    served_name="qwen3.6-40b",
    server="llama_cpp",
    quantization="gguf",
    gguf_quant="Q5_K_M",
    ready=False,
    # Build profile: a large, creative, uncensored "MAX" thinking finetune.
    # DavidAU's MAX imatrix builds like a wider top_k (40) and a little min_p;
    # the thinking trace is long, so this gets the biggest completion budget.
    # It's verbose and loves alternatives, so rein it in hard via guidance and
    # bounce prose-only turns more. GGUF on llama.cpp → no forced tool_choice.
    sampling={
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 40,
        "min_p": 0.05,
        "presence_penalty": 0.5,
    },
    max_completion_tokens=16384,
    stall_nudges=3,
    think_tags=("think", "thinking"),
    supports_forced_tool_choice=False,
    tool_guidance=(
        "You are a large, creative, uncensored coder finetune with thinking "
        "enabled. Rein it in for agent work:\n"
        "- Keep your reasoning short and decisive, then act. Do not write essays "
        "or lay out several alternative solutions; pick one and execute it with "
        "a tool call.\n"
        "- One concrete step per turn, and verify with a tool before claiming "
        "success. Never paste code or commands as your message — run them "
        "yourself.\n"
        "- Your final answer is plain prose for a human on a phone: short, no raw "
        "JSON or tool syntax. Respect the operator y/n gates and keep all "
        "internet on the phone."
    ),
    notes_extra=[
        "First serve builds llama.cpp with CUDA on the box (needs the CUDA "
        "toolkit / nvcc — use a CUDA-devel image, not runtime-only).",
        "Large community uncensored finetune — sanity-check its tool-calling "
        "discipline before trusting it with host writes.",
    ],
)

# Order is the picker order; HERMES first as the ready default.
CATALOG: dict[str, ModelSpec] = {
    HERMES.key: HERMES,
    QWEN_OFFICIAL.key: QWEN_OFFICIAL,
    QWEN.key: QWEN,
    QWEN_40B.key: QWEN_40B,
}
DEFAULT_KEY = HERMES.key


def model_list() -> list[ModelSpec]:
    return list(CATALOG.values())


def get_spec(key: str) -> ModelSpec:
    return CATALOG.get(key or DEFAULT_KEY, HERMES)


def resolve(cfg) -> ModelSpec:
    """The model the config currently points at (defaults to Hermes)."""
    return get_spec(cfg.get("model_id", DEFAULT_KEY))
