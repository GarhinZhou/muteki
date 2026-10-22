/**
 * Small dependency-free syntax highlighter for solve EXP scripts.
 *
 * It is intentionally a tokenizer, not a parser: comments, strings, numbers,
 * keywords/builtins, function calls and shell/perl/php variables. The token
 * text always concatenates back to the exact input, so copying/downloading the
 * original source is unaffected.
 */

export type HighlightTokenType =
  | "plain"
  | "comment"
  | "string"
  | "number"
  | "keyword"
  | "builtin"
  | "function"
  | "variable";

export interface HighlightToken {
  type: HighlightTokenType;
  text: string;
}

interface LanguageRules {
  keywords: Set<string>;
  builtins: Set<string>;
  lineComment?: string;
  blockComment?: boolean;
  strings: string[];
  tripleQuote?: boolean;
  variables?: boolean;
}

const words = (value: string): Set<string> => new Set(value.split(/\s+/).filter(Boolean));

const PY_KEYWORDS = words(
  "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case",
);
const PY_BUILTINS = words(
  "abs all any ascii bin bool bytearray bytes callable chr classmethod compile complex delattr dict dir divmod enumerate eval exec filter float format frozenset getattr globals hasattr hash help hex id input int isinstance issubclass iter len list locals map max memoryview min next object oct open ord pow print property range repr reversed round set setattr slice sorted staticmethod str sum super tuple type vars zip Exception ValueError TypeError RuntimeError",
);

const BASH_KEYWORDS = words(
  "if then else elif fi for while until do done case esac function in select time coproc return exit export local readonly declare typeset unset shift source alias break continue",
);
const BASH_BUILTINS = words(
  "echo printf read cd pwd test true false grep sed awk cut sort uniq head tail cat ls chmod chown curl wget nc python python3 bash sh sudo find xargs timeout",
);

const C_KEYWORDS = words(
  "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while",
);
const C_BUILTINS = words(
  "printf scanf malloc free memcpy memset strlen strcmp fopen fread fwrite system execve socket connect",
);
const CPP_KEYWORDS = words(
  "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while class public private protected virtual template typename namespace using new delete this nullptr true false try catch throw",
);
const JS_KEYWORDS = words(
  "break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof let new return super switch this throw try typeof var void while with yield async await of static get set",
);
const JS_BUILTINS = words(
  "console document window require module exports process JSON Math Array Object String Number Boolean Promise fetch setTimeout setInterval",
);
const GO_KEYWORDS = words(
  "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var",
);
const GO_BUILTINS = words(
  "fmt println print len make new append panic recover",
);
const RUST_KEYWORDS = words(
  "as break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while async await dyn",
);
const RUST_BUILTINS = words(
  "println print vec Some None Ok Err String Vec",
);
const RUBY_KEYWORDS = words(
  "BEGIN END alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield",
);
const PHP_KEYWORDS = words(
  "abstract and array as break callable case catch class clone const continue declare default die do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile eval exit extends final finally fn for foreach function global goto if implements include include_once instanceof insteadof interface isset list match namespace new or print private protected public readonly require require_once return static switch throw trait try unset use var while xor yield",
);
const PERL_KEYWORDS = words(
  "my our local sub if elsif else unless while until for foreach do last next redo return use require package print say",
);
const ASM_KEYWORDS = words(
  "mov lea push pop call ret jmp je jne jz jnz ja jb jg jl add sub xor and or cmp test syscall int leave nop",
);

const GENERIC = {
  keywords: words(
    "if else for while do switch case break continue return function class def import from in new try catch finally true false null nil None True False",
  ),
  builtins: words("print echo len range open input str int float list dict set"),
  lineComment: "#",
  strings: ["'", '"'],
  tripleQuote: true,
} satisfies LanguageRules;

const RULES: Record<string, LanguageRules> = {
  python: {
    keywords: PY_KEYWORDS,
    builtins: PY_BUILTINS,
    lineComment: "#",
    strings: ["'", '"'],
    tripleQuote: true,
  },
  bash: {
    keywords: BASH_KEYWORDS,
    builtins: BASH_BUILTINS,
    lineComment: "#",
    strings: ["'", '"', "`"],
    variables: true,
  },
  c: {
    keywords: C_KEYWORDS,
    builtins: C_BUILTINS,
    lineComment: "//",
    blockComment: true,
    strings: ["'", '"'],
  },
  cpp: {
    keywords: CPP_KEYWORDS,
    builtins: C_BUILTINS,
    lineComment: "//",
    blockComment: true,
    strings: ["'", '"'],
  },
  javascript: {
    keywords: JS_KEYWORDS,
    builtins: JS_BUILTINS,
    lineComment: "//",
    blockComment: true,
    strings: ["'", '"', "`"],
    variables: true,
  },
  typescript: {
    keywords: JS_KEYWORDS,
    builtins: JS_BUILTINS,
    lineComment: "//",
    blockComment: true,
    strings: ["'", '"', "`"],
    variables: true,
  },
  go: {
    keywords: GO_KEYWORDS,
    builtins: GO_BUILTINS,
    lineComment: "//",
    blockComment: true,
    strings: ["'", '"', "`"],
  },
  rust: {
    keywords: RUST_KEYWORDS,
    builtins: RUST_BUILTINS,
    lineComment: "//",
    blockComment: true,
    strings: ['"'],
  },
  ruby: {
    keywords: RUBY_KEYWORDS,
    builtins: words("puts print p gets require attr_accessor"),
    lineComment: "#",
    strings: ["'", '"'],
    variables: true,
  },
  php: {
    keywords: PHP_KEYWORDS,
    builtins: words("echo print isset empty count strlen system shell_exec exec"),
    lineComment: "//",
    blockComment: true,
    strings: ["'", '"'],
    variables: true,
  },
  perl: {
    keywords: PERL_KEYWORDS,
    builtins: words("print say chomp split join push pop shift unshift keys values"),
    lineComment: "#",
    strings: ["'", '"'],
    variables: true,
  },
  asm: {
    keywords: ASM_KEYWORDS,
    builtins: words("rax rbx rcx rdx rsi rdi rbp rsp r8 r9 r10 r11 r12 r13 r14 r15 eax ebx ecx edx"),
    lineComment: ";",
    strings: ['"'],
  },
  generic: GENERIC,
};

export function normalizeLanguage(language?: string): string {
  const value = String(language || "").trim().toLowerCase();
  if (["py", "python", "python3", "py3"].includes(value)) return "python";
  if (["sh", "bash", "shell", "zsh"].includes(value)) return "bash";
  if (["c", "h"].includes(value)) return "c";
  if (["cpp", "c++", "cc", "cxx", "hpp", "hxx"].includes(value)) return "cpp";
  if (["js", "javascript", "node", "mjs", "cjs"].includes(value)) return "javascript";
  if (["ts", "typescript", "tsx"].includes(value)) return "typescript";
  if (["go", "golang"].includes(value)) return "go";
  if (["rs", "rust"].includes(value)) return "rust";
  if (["rb", "ruby"].includes(value)) return "ruby";
  if (["php"].includes(value)) return "php";
  if (["pl", "perl"].includes(value)) return "perl";
  if (["asm", "nasm", "s"].includes(value)) return "asm";
  if (value in RULES) return value;
  return "generic";
}

function isIdentStart(ch: string): boolean {
  return /[A-Za-z_$]/.test(ch);
}

function isIdentPart(ch: string): boolean {
  return /[A-Za-z0-9_$]/.test(ch);
}

function numberEnd(code: string, start: number): number {
  const match = code.slice(start).match(
    /^(?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)/,
  );
  return match ? start + match[0].length : start + 1;
}

export function highlightCode(code: string, language?: string): HighlightToken[] {
  const rules = RULES[normalizeLanguage(language)] || RULES.generic;
  const tokens: HighlightToken[] = [];
  let plain = "";
  let i = 0;

  const push = (type: HighlightTokenType, text: string) => {
    if (!text) return;
    if (plain) {
      tokens.push({ type: "plain", text: plain });
      plain = "";
    }
    tokens.push({ type, text });
  };

  while (i < code.length) {
    const ch = code[i];

    if (rules.blockComment && code.startsWith("/*", i)) {
      const end = code.indexOf("*/", i + 2);
      const next = end === -1 ? code.length : end + 2;
      push("comment", code.slice(i, next));
      i = next;
      continue;
    }

    if (rules.lineComment && code.startsWith(rules.lineComment, i)) {
      const end = code.indexOf("\n", i);
      const next = end === -1 ? code.length : end;
      push("comment", code.slice(i, next));
      i = next;
      continue;
    }

    if (
      rules.tripleQuote
      && (code.startsWith('"""', i) || code.startsWith("'''", i))
    ) {
      const marker = code.slice(i, i + 3);
      const end = code.indexOf(marker, i + 3);
      const next = end === -1 ? code.length : end + 3;
      push("string", code.slice(i, next));
      i = next;
      continue;
    }

    if (rules.strings.includes(ch)) {
      let next = i + 1;
      while (next < code.length) {
        if (code[next] === "\\") {
          next += 2;
          continue;
        }
        if (code[next] === ch) {
          next += 1;
          break;
        }
        next += 1;
      }
      push("string", code.slice(i, next));
      i = next;
      continue;
    }

    if (rules.variables && (ch === "$" || ch === "@" || ch === "%")) {
      if (ch === "$" && code[i + 1] === "{") {
        const end = code.indexOf("}", i + 2);
        if (end !== -1) {
          push("variable", code.slice(i, end + 1));
          i = end + 1;
          continue;
        }
      }
      if (i + 1 < code.length && /[A-Za-z_0-9?@#*$!-]/.test(code[i + 1])) {
        let next = i + 2;
        while (next < code.length && /[A-Za-z0-9_]/.test(code[next])) next += 1;
        push("variable", code.slice(i, next));
        i = next;
        continue;
      }
    }

    if (/[0-9]/.test(ch) && (i === 0 || !isIdentPart(code[i - 1]))) {
      const next = numberEnd(code, i);
      push("number", code.slice(i, next));
      i = next;
      continue;
    }

    if (isIdentStart(ch)) {
      let next = i + 1;
      while (next < code.length && isIdentPart(code[next])) next += 1;
      const word = code.slice(i, next);
      let after = next;
      while (after < code.length && (code[after] === " " || code[after] === "\t")) {
        after += 1;
      }
      if (rules.keywords.has(word)) {
        push("keyword", word);
      } else if (rules.builtins.has(word)) {
        push("builtin", word);
      } else if (code[after] === "(") {
        push("function", word);
      } else {
        plain += word;
      }
      i = next;
      continue;
    }

    plain += ch;
    i += 1;
  }

  if (plain) tokens.push({ type: "plain", text: plain });
  return tokens;
}
