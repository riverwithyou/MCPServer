# tools/calc/Calc.py
import ast
import math
import operator
import sympy as sp
import json
from typing import Dict, Any, List

def calculate_expression(expression: str) -> Dict[str, Any]:
    """
    计算数学表达式的结果
    
    参数:
        expression: 数学表达式字符串
    
    返回:
        包含结果的字典
    """
    try:
        # 清理表达式
        expression = expression.strip()
        if not expression:
            return {
                "success": False,
                "error": "表达式不能为空",
                "type": "expression"
            }
        
        # 允许的安全函数和常量
        safe_dict = {
            'abs': abs,
            'round': round,
            'min': min,
            'max': max,
            'sum': sum,
            'pow': pow,
            'sqrt': math.sqrt,
            'sin': math.sin,
            'cos': math.cos,
            'tan': math.tan,
            'asin': math.asin,
            'acos': math.acos,
            'atan': math.atan,
            'sinh': math.sinh,
            'cosh': math.cosh,
            'tanh': math.tanh,
            'log': math.log,
            'log10': math.log10,
            'log2': math.log2,
            'exp': math.exp,
            'ceil': math.ceil,
            'floor': math.floor,
            'fabs': math.fabs,
            'degrees': math.degrees,
            'radians': math.radians,
            'pi': math.pi,
            'e': math.e,
            'tau': math.tau,
            '+': operator.add,
            '-': operator.sub,
            '*': operator.mul,
            '/': operator.truediv,
            '//': operator.floordiv,
            '%': operator.mod,
            '**': operator.pow,
        }
        
        result = eval(expression, {"__builtins__": {}}, safe_dict)
        
        # 格式化结果
        if isinstance(result, float):
            if result.is_integer():
                result = int(result)
        
        return {
            "success": True,
            "type": "expression",
            "expression": expression,
            "result": result,
            "result_type": type(result).__name__
        }
            
    except ZeroDivisionError:
        return {
            "success": False,
            "error": "除零错误",
            "type": "expression"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "type": "expression"
        }


def solve_equation(equation: str, variable: str = None) -> Dict[str, Any]:
    """
    解方程（支持一元方程、多元方程、方程组）
    
    参数:
        equation: 方程表达式，例如 "x**2 - 4 = 0" 或 "2*x + 3*y = 10"
        variable: 变量名（可选），如果不指定会自动识别
    
    返回:
        包含解的字典
    """
    try:
        # 处理方程组（用分号或换行分隔）
        if ';' in equation or '\n' in equation:
            return solve_system(equation, variable)
        
        # 处理等式
        if '=' in equation:
            left, right = equation.split('=', 1)
            expr = f"({left}) - ({right})"
        else:
            # 如果没有等号，默认为等于0
            expr = equation
        
        # 解析表达式
        sympy_expr = sp.sympify(expr)
        
        # 识别变量
        variables = list(sympy_expr.free_symbols)
        if not variables:
            # 没有变量，直接计算表达式
            result = float(sympy_expr)
            return {
                "success": True,
                "type": "equation",
                "equation": equation,
                "result": result,
                "message": "表达式没有变量，直接计算结果"
            }
        
        # 如果指定了变量，或者只有一个变量
        if variable:
            var = sp.Symbol(variable)
            if var not in variables:
                return {
                    "success": False,
                    "error": f"变量 '{variable}' 不在方程中",
                    "available_variables": [str(v) for v in variables],
                    "type": "equation"
                }
        else:
            if len(variables) == 1:
                var = variables[0]
            else:
                return {
                    "success": False,
                    "error": "方程包含多个变量，请指定要求解的变量",
                    "available_variables": [str(v) for v in variables],
                    "type": "equation"
                }
        
        # 解方程
        try:
            # 先尝试判断是否为恒等式
            simplified = sp.simplify(sympy_expr)
            if simplified == 0:
                return {
                    "success": True,
                    "type": "equation",
                    "equation": equation,
                    "variable": str(var),
                    "solutions": "所有实数都成立（恒等式）",
                    "solution_count": "无限",
                    "is_real": True,
                    "message": f"方程恒成立，{str(var)} 可以为任意实数"
                }
            
            # 检查是否矛盾方程
            if simplified.is_constant() and simplified != 0:
                return {
                    "success": True,
                    "type": "equation",
                    "equation": equation,
                    "variable": str(var),
                    "solutions": [],
                    "solution_count": 0,
                    "is_real": True,
                    "message": f"方程无解（矛盾方程：{simplified} = 0 不可能成立）"
                }
        except:
            pass
        
        solutions = sp.solve(sympy_expr, var)
        
        # 格式化结果
        formatted_solutions = []
        for sol in solutions:
            if sol.is_real:
                formatted_solutions.append(float(sol))
            else:
                formatted_solutions.append(str(sol))
        
        result = {
            "success": True,
            "type": "equation",
            "equation": equation,
            "variable": str(var),
            "solutions": formatted_solutions,
            "solution_count": len(formatted_solutions),
            "is_real": all(sol.is_real for sol in solutions if hasattr(sol, 'is_real'))
        }
        
        # 添加额外的数学信息
        if len(formatted_solutions) == 0:
            result["message"] = "方程无解"
        elif len(formatted_solutions) == 1:
            result["message"] = f"方程的解为 {formatted_solutions[0]}"
        else:
            result["message"] = f"方程有 {len(formatted_solutions)} 个解"
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "type": "equation"
        }


def solve_system(equations_str: str, variables_str: str = None) -> Dict[str, Any]:
    """
    解方程组
    
    参数:
        equations_str: 方程组字符串，用分号或换行分隔
        variables_str: 变量列表，用逗号分隔，例如 "x,y"
    
    返回:
        包含解的字典
    """
    try:
        # 分割方程
        if ';' in equations_str:
            equations_list = [eq.strip() for eq in equations_str.split(';')]
        else:
            equations_list = [eq.strip() for eq in equations_str.split('\n') if eq.strip()]
        
        # 解析方程
        sympy_eqs = []
        for eq in equations_list:
            if '=' in eq:
                left, right = eq.split('=', 1)
                sympy_eqs.append(sp.Eq(sp.sympify(left), sp.sympify(right)))
            else:
                sympy_eqs.append(sp.Eq(sp.sympify(eq), 0))
        
        # 识别变量
        all_vars = set()
        for eq in sympy_eqs:
            all_vars.update(eq.free_symbols)
        
        # 解析变量列表
        if variables_str:
            variables = [sp.Symbol(v.strip()) for v in variables_str.split(',')]
        else:
            variables = list(all_vars)
        
        # 解方程组
        solutions = sp.solve(sympy_eqs, variables)
        
        # 格式化结果
        if not solutions:
            return {
                "success": False,
                "error": "方程组无解",
                "type": "system"
            }
        
        # 处理多个解的情况
        if isinstance(solutions, list):
            # 多个解
            formatted_solutions = []
            for sol in solutions:
                if isinstance(sol, (tuple, list)):
                    sol_dict = {}
                    for i, var in enumerate(variables):
                        val = sol[i]
                        if val.is_real:
                            sol_dict[str(var)] = float(val)
                        else:
                            sol_dict[str(var)] = str(val)
                    formatted_solutions.append(sol_dict)
                else:
                    # 单个解
                    if sol.is_real:
                        formatted_solutions.append(float(sol))
                    else:
                        formatted_solutions.append(str(sol))
            
            result = {
                "success": True,
                "type": "system",
                "equations": equations_list,
                "variables": [str(v) for v in variables],
                "solutions": formatted_solutions,
                "solution_count": len(formatted_solutions)
            }
        else:
            # 单个解
            result_dict = {}
            for var, val in solutions.items():
                if val.is_real:
                    result_dict[str(var)] = float(val)
                else:
                    result_dict[str(var)] = str(val)
            
            result = {
                "success": True,
                "type": "system",
                "equations": equations_list,
                "variables": [str(v) for v in variables],
                "solution": result_dict,
                "solution_count": 1
            }
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "type": "system"
        }


def solve_inequality(inequality: str, variable: str = None) -> Dict[str, Any]:
    """
    解不等式
    
    参数:
        inequality: 不等式表达式，例如 "x**2 - 4 > 0"
        variable: 变量名（可选）
    
    返回:
        包含解集的字典
    """
    try:
        # 解析不等式
        if '>' in inequality:
            if '>=' in inequality:
                left, right = inequality.split('>=', 1)
                relation = '>='
            else:
                left, right = inequality.split('>', 1)
                relation = '>'
        elif '<' in inequality:
            if '<=' in inequality:
                left, right = inequality.split('<=', 1)
                relation = '<='
            else:
                left, right = inequality.split('<', 1)
                relation = '<'
        else:
            return {
                "success": False,
                "error": "无法识别不等号",
                "type": "inequality"
            }
        
        expr_left = sp.sympify(left)
        expr_right = sp.sympify(right)
        
        # 创建不等式
        if relation == '>':
            inequality_obj = expr_left > expr_right
        elif relation == '>=':
            inequality_obj = expr_left >= expr_right
        elif relation == '<':
            inequality_obj = expr_left < expr_right
        else:
            inequality_obj = expr_left <= expr_right
        
        # 识别变量
        variables = list(inequality_obj.free_symbols)
        if not variables:
            # 没有变量，直接判断
            truth_value = bool(inequality_obj)
            return {
                "success": True,
                "type": "inequality",
                "inequality": inequality,
                "result": truth_value,
                "message": f"不等式{'成立' if truth_value else '不成立'}"
            }
        
        # 如果指定了变量，或者只有一个变量
        if variable:
            var = sp.Symbol(variable)
        else:
            if len(variables) == 1:
                var = variables[0]
            else:
                return {
                    "success": False,
                    "error": "不等式包含多个变量，请指定要求解的变量",
                    "available_variables": [str(v) for v in variables],
                    "type": "inequality"
                }
        
        # 解不等式
        solution = sp.solve_univariate_inequality(inequality_obj, var)
        
        return {
            "success": True,
            "type": "inequality",
            "inequality": inequality,
            "variable": str(var),
            "solution": str(solution),
            "solution_set": solution.__str__(),
            "message": f"不等式的解集为 {solution}"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "type": "inequality"
        }


def calculate_limit(expression: str, variable: str, point: str, direction: str = None) -> Dict[str, Any]:
    """
    计算极限
    
    参数:
        expression: 表达式，例如 "sin(x)/x"
        variable: 变量名，例如 "x"
        point: 趋近的点，例如 "0" 或 "oo" (无穷大)
        direction: 方向，可选 '+' (右极限), '-' (左极限), None (双向)
    
    返回:
        包含极限结果的字典
    """
    try:
        var = sp.Symbol(variable)
        expr = sp.sympify(expression)
        
        # 处理无穷大
        if point == 'oo' or point == 'inf':
            point_obj = sp.oo
        elif point == '-oo' or point == '-inf':
            point_obj = -sp.oo
        else:
            point_obj = sp.sympify(point)
        
        # 计算极限
        if direction == '+':
            limit_result = sp.limit(expr, var, point_obj, dir='+')
        elif direction == '-':
            limit_result = sp.limit(expr, var, point_obj, dir='-')
        else:
            limit_result = sp.limit(expr, var, point_obj)
        
        # 格式化结果
        if limit_result == sp.oo:
            result_str = "∞"
        elif limit_result == -sp.oo:
            result_str = "-∞"
        else:
            result_str = float(limit_result) if limit_result.is_real else str(limit_result)
        
        return {
            "success": True,
            "type": "limit",
            "expression": expression,
            "variable": variable,
            "point": point,
            "direction": direction if direction else "双向",
            "result": result_str,
            "result_simplified": str(limit_result)
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "type": "limit"
        }


def calculate_derivative(expression: str, variable: str, order: int = 1, evaluate_at: str = None) -> Dict[str, Any]:
    """
    计算导数/微分
    
    参数:
        expression: 表达式，例如 "x**2 + sin(x)"
        variable: 变量名，例如 "x"
        order: 阶数，1表示一阶导，2表示二阶导，以此类推
        evaluate_at: 可选，在某点的值，例如 "0" 或 "pi/2"
    
    返回:
        包含导数结果的字典
    """
    try:
        var = sp.Symbol(variable)
        expr = sp.sympify(expression)
        
        # 计算导数
        derivative = sp.diff(expr, var, order)
        
        result = {
            "success": True,
            "type": "derivative",
            "expression": expression,
            "variable": variable,
            "order": order,
            "derivative": str(derivative),
            "derivative_simplified": str(sp.simplify(derivative))
        }
        
        # 如果在特定点求值
        if evaluate_at:
            point = sp.sympify(evaluate_at)
            value = derivative.subs(var, point)
            result["evaluate_at"] = evaluate_at
            result["value"] = float(value) if value.is_real else str(value)
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "type": "derivative"
        }


def calculate_integral(expression: str, variable: str, lower_limit: str = None, upper_limit: str = None) -> Dict[str, Any]:
    """
    计算积分
    
    参数:
        expression: 表达式，例如 "x**2"
        variable: 变量名，例如 "x"
        lower_limit: 可选，下限（定积分）
        upper_limit: 可选，上限（定积分）
    
    返回:
        包含积分结果的字典
    """
    try:
        var = sp.Symbol(variable)
        expr = sp.sympify(expression)
        
        if lower_limit is not None and upper_limit is not None:
            # 定积分
            lower = sp.sympify(lower_limit)
            upper = sp.sympify(upper_limit)
            integral = sp.integrate(expr, (var, lower, upper))
            
            result = {
                "success": True,
                "type": "definite_integral",
                "expression": expression,
                "variable": variable,
                "lower_limit": lower_limit,
                "upper_limit": upper_limit,
                "result": float(integral) if integral.is_real else str(integral),
                "result_exact": str(integral)
            }
        else:
            # 不定积分
            integral = sp.integrate(expr, var)
            result = {
                "success": True,
                "type": "indefinite_integral",
                "expression": expression,
                "variable": variable,
                "result": str(integral) + " + C",
                "result_simplified": str(sp.simplify(integral)) + " + C"
            }
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "type": "integral"
        }


def taylor_series(expression: str, variable: str, point: str = "0", order: int = 5) -> Dict[str, Any]:
    """
    泰勒级数展开
    
    参数:
        expression: 表达式，例如 "sin(x)"
        variable: 变量名，例如 "x"
        point: 展开点，默认 "0" (麦克劳林级数)
        order: 展开阶数
    
    返回:
        包含泰勒级数的字典
    """
    try:
        var = sp.Symbol(variable)
        expr = sp.sympify(expression)
        point_obj = sp.sympify(point)
        
        # 计算泰勒级数
        series = sp.series(expr, var, point_obj, order)
        
        return {
            "success": True,
            "type": "taylor_series",
            "expression": expression,
            "variable": variable,
            "point": point,
            "order": order,
            "series": str(series),
            "series_removed_o": str(series.removeO())
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "type": "taylor_series"
        }


def handle_calc(arguments: Dict[str, Any]) -> str:
    """
    处理计算请求的主处理器
    """
    # 获取操作类型
    operation = arguments.get("operation", "expression")
    
    if operation == "expression":
        # 普通表达式计算
        expression = arguments.get("expression", "")
        if expression == "":
            return json.dumps({
                "error": "请提供 expression 参数",
                "usage": {
                    "operation": "expression",
                    "expression": "2 + 3 * 4"
                }
            }, ensure_ascii=False, indent=2)
        
        result = calculate_expression(expression)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    elif operation == "equation":
        # 解方程
        equation = arguments.get("equation", "")
        variable = arguments.get("variable", None)
        
        if equation == "":
            return json.dumps({
                "error": "请提供 equation 参数",
                "usage": {
                    "operation": "equation",
                    "equation": "x**2 - 4 = 0",
                    "variable": "x (可选)"
                },
                "examples": [
                    "一元一次方程: 2*x + 3 = 7",
                    "一元二次方程: x**2 - 5*x + 6 = 0",
                    "带根号: sqrt(x) = 2",
                    "三角函数: sin(x) = 0.5",
                    "方程组: x + y = 5; x - y = 1"
                ]
            }, ensure_ascii=False, indent=2)
        
        result = solve_equation(equation, variable)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    elif operation == "inequality":
        # 解不等式
        inequality = arguments.get("inequality", "")
        variable = arguments.get("variable", None)
        
        if inequality == "":
            return json.dumps({
                "error": "请提供 inequality 参数",
                "usage": {
                    "operation": "inequality",
                    "inequality": "x**2 - 4 > 0",
                    "variable": "x (可选)"
                }
            }, ensure_ascii=False, indent=2)
        
        result = solve_inequality(inequality, variable)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    elif operation == "limit":
        # 计算极限
        expression = arguments.get("expression", "")
        variable = arguments.get("variable", "")
        point = arguments.get("point", "")
        direction = arguments.get("direction", None)
        
        if not all([expression, variable, point]):
            return json.dumps({
                "error": "请提供 expression, variable, point 参数",
                "usage": {
                    "operation": "limit",
                    "expression": "sin(x)/x",
                    "variable": "x",
                    "point": "0",
                    "direction": "可选: '+' (右极限), '-' (左极限)"
                },
                "examples": [
                    "基本极限: sin(x)/x 在 x->0",
                    "无穷极限: 1/x 在 x->oo",
                    "左极限: 1/x 在 x->0-"
                ]
            }, ensure_ascii=False, indent=2)
        
        result = calculate_limit(expression, variable, point, direction)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    elif operation == "derivative":
        # 计算导数
        expression = arguments.get("expression", "")
        variable = arguments.get("variable", "")
        order = arguments.get("order", 1)
        evaluate_at = arguments.get("evaluate_at", None)
        
        if not all([expression, variable]):
            return json.dumps({
                "error": "请提供 expression 和 variable 参数",
                "usage": {
                    "operation": "derivative",
                    "expression": "x**2 + sin(x)",
                    "variable": "x",
                    "order": "1 (可选，默认1)",
                    "evaluate_at": "可选，在某点的值"
                },
                "examples": [
                    "一阶导: x**2 的导数是 2x",
                    "二阶导: x**3 的二阶导是 6x",
                    "在某点求导: x**2 在 x=3 处的导数是 6"
                ]
            }, ensure_ascii=False, indent=2)
        
        result = calculate_derivative(expression, variable, order, evaluate_at)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    elif operation == "integral":
        # 计算积分
        expression = arguments.get("expression", "")
        variable = arguments.get("variable", "")
        lower_limit = arguments.get("lower_limit", None)
        upper_limit = arguments.get("upper_limit", None)
        
        if not all([expression, variable]):
            return json.dumps({
                "error": "请提供 expression 和 variable 参数",
                "usage": {
                    "operation": "integral",
                    "expression": "x**2",
                    "variable": "x",
                    "lower_limit": "可选，下限",
                    "upper_limit": "可选，上限"
                },
                "examples": [
                    "不定积分: x**2 的积分是 x**3/3 + C",
                    "定积分: x**2 从0到1的积分是 1/3"
                ]
            }, ensure_ascii=False, indent=2)
        
        result = calculate_integral(expression, variable, lower_limit, upper_limit)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    elif operation == "taylor":
        # 泰勒级数
        expression = arguments.get("expression", "")
        variable = arguments.get("variable", "")
        point = arguments.get("point", "0")
        order = arguments.get("order", 5)
        
        if not all([expression, variable]):
            return json.dumps({
                "error": "请提供 expression 和 variable 参数",
                "usage": {
                    "operation": "taylor",
                    "expression": "sin(x)",
                    "variable": "x",
                    "point": "0 (可选，默认0)",
                    "order": "5 (可选，默认5)"
                }
            }, ensure_ascii=False, indent=2)
        
        result = taylor_series(expression, variable, point, order)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    else:
        return json.dumps({
            "error": f"不支持的操作类型: {operation}",
            "supported_operations": [
                "expression", "equation", "inequality", 
                "limit", "derivative", "integral", "taylor"
            ],
            "usage": {
                "计算表达式": {
                    "operation": "expression",
                    "expression": "sqrt(16) + sin(pi/2)"
                },
                "解方程": {
                    "operation": "equation",
                    "equation": "x**2 - 4 = 0",
                    "variable": "x"
                },
                "解不等式": {
                    "operation": "inequality",
                    "inequality": "x**2 - 4 > 0",
                    "variable": "x"
                },
                "求极限": {
                    "operation": "limit",
                    "expression": "sin(x)/x",
                    "variable": "x",
                    "point": "0"
                },
                "求导数": {
                    "operation": "derivative",
                    "expression": "x**2",
                    "variable": "x",
                    "order": 1
                },
                "求积分": {
                    "operation": "integral",
                    "expression": "x**2",
                    "variable": "x",
                    "lower_limit": "0",
                    "upper_limit": "1"
                },
                "泰勒展开": {
                    "operation": "taylor",
                    "expression": "sin(x)",
                    "variable": "x",
                    "point": "0",
                    "order": 5
                }
            }
        }, ensure_ascii=False, indent=2)