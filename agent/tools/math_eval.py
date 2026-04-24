"""
Tool di calcolo matematico sicuro per l'Executor sandbox di Euri.
Usa ast per validare l'espressione — zero eval() su stringhe libere.
"""
import ast
import math
import operator
from agent.executor import ToolResult

# Operatori binari permessi
_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}

# Operatori unari permessi
_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Funzioni matematiche permesse (dal modulo math)
_FUNCTIONS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "ceil": math.ceil,
    "floor": math.floor,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "pi": math.pi,
    "e": math.e,
}


def _eval_node(node):
    """Valuta ricorsivamente un nodo AST. Lancia ValueError se il nodo non è permesso."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Costante non permessa: {node.value!r}")

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BINOPS:
            raise ValueError(f"Operatore non permesso: {op_type.__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if op_type == ast.Div and right == 0:
            raise ValueError("Divisione per zero")
        if op_type == ast.Pow and abs(right) > 100:
            raise ValueError("Esponente troppo grande")
        return _BINOPS[op_type](left, right)

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARYOPS:
            raise ValueError(f"Operatore unario non permesso: {op_type.__name__}")
        return _UNARYOPS[op_type](_eval_node(node.operand))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Chiamata non permessa")
        fname = node.func.id
        if fname not in _FUNCTIONS:
            raise ValueError(f"Funzione non permessa: {fname!r}")
        args = [_eval_node(a) for a in node.args]
        # round(x, n) accetta 2 argomenti — il secondo deve essere intero
        if fname == "round" and len(args) == 2:
            return round(args[0], int(args[1]))
        return _FUNCTIONS[fname](*args)

    if isinstance(node, ast.Name):
        if node.id in _FUNCTIONS:
            return _FUNCTIONS[node.id]  # costanti come pi, e
        raise ValueError(f"Nome non permesso: {node.id!r}")

    raise ValueError(f"Nodo AST non permesso: {type(node).__name__}")


def _safe_eval(expression: str) -> float:
    """Valuta un'espressione matematica in modo sicuro tramite AST."""
    # Normalizza separatori decimali: sostituisce virgola con punto SOLO fuori dalle parentesi
    # (dentro le parentesi la virgola è separatore di argomenti, non decimale)
    expression = expression.strip()
    result_chars = []
    depth = 0
    for ch in expression:
        if ch == '(':
            depth += 1
            result_chars.append(ch)
        elif ch == ')':
            depth -= 1
            result_chars.append(ch)
        elif ch == ',' and depth == 0:
            result_chars.append('.')
        else:
            result_chars.append(ch)
    expression = ''.join(result_chars)
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Espressione non valida: {e}")
    return _eval_node(tree)


def _format_result(value: float) -> str:
    """Formatta il risultato in modo leggibile a voce."""
    if value == int(value) and abs(value) < 1e12:
        return str(int(value))
    # Fino a 6 cifre significative, rimuovi zeri finali
    formatted = f"{value:.6g}"
    return formatted


def tool_evaluate_math(params: dict, **kwargs) -> ToolResult:
    """
    Calcola un'espressione matematica in modo sicuro.
    Supporta: + - * / ** % // sqrt abs round ceil floor log sin cos tan pi e
    """
    expression = params.get("expression", "").strip()
    if not expression:
        return ToolResult(success=False, output="Nessuna espressione fornita.", error="empty expression")

    try:
        result = _safe_eval(expression)
        readable = _format_result(result)

        # Copia il risultato numerico negli appunti per incollarlo subito
        try:
            import pyperclip
            pyperclip.copy(readable)
            clipboard_msg = " Copiato negli appunti."
        except Exception:
            clipboard_msg = ""

        output = f"Il risultato di {expression} è {readable}.{clipboard_msg}"
        return ToolResult(
            success=True,
            output=output,
            raw_data={"expression": expression, "result": result}
        )
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        return ToolResult(
            success=False,
            output=f"Non riesco a calcolare '{expression}'. {e}",
            error=str(e)
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output="Errore nel calcolo.",
            error=str(e)
        )
