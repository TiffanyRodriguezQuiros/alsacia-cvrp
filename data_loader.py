import re
import pandas as pd

RUTA_EXCEL = "data/Caso_Final_II1122_Datos.xlsx"

log_limpieza = []


def limpiar_clientes(df):
    duplicados = df[df.duplicated(subset="ID", keep=False)]
    if not duplicados.empty:
        ids_dup = duplicados["ID"].unique().tolist()
        df = df.sort_values("Demanda_Cajas", ascending=False).drop_duplicates(subset="ID", keep="first")
        log_limpieza.append(f"ID {ids_dup}: duplicado, se conservó la fila de mayor demanda.")
    df = df.sort_values("ID").reset_index(drop=True)
    return df


def limpiar_ventanas(df):
    invertidas = df["Ventana_Fin_min"] < df["Ventana_Ini_min"]
    if invertidas.any():
        ids_inv = df.loc[invertidas, "ID"].tolist()
        df.loc[invertidas, "Ventana_Ini_min"] = 0
        df.loc[invertidas, "Ventana_Fin_min"] = 720
        log_limpieza.append(f"ID {ids_inv}: ventana invertida, se reemplazó por el día completo (0-720).")
    return df


def revisar_demanda_cero(df):
    sin_pedido = df[(df["Demanda_Cajas"] == 0) & (df["ID"] != 0)]
    if not sin_pedido.empty:
        ids_cero = sin_pedido["ID"].tolist()
        log_limpieza.append(f"ID {ids_cero}: demanda 0, se interpreta como 'sin pedido este día', no como dato faltante.")
    return df


def limpiar_distancias(matriz_np):
    n = matriz_np.shape[0]
    for i in range(n):
        for j in range(i + 1, n):
            if abs(matriz_np[i, j] - matriz_np[j, i]) > 0.05:
                promedio = (matriz_np[i, j] + matriz_np[j, i]) / 2
                matriz_np[i, j] = promedio
                matriz_np[j, i] = promedio
                log_limpieza.append(f"Nodos ({i},{j}): distancia asimétrica, se promedió.")
    return matriz_np


def limpiar_parametros(param_df):
    crudo = dict(zip(param_df["Parametro"], param_df["Valor"]))

    def sacar_numero(texto):
        match = re.search(r"[-+]?\d*\.?\d+", str(texto))
        return float(match.group())

    return {
        "jornada_max_min": sacar_numero(crudo["Jornada máxima por vehículo (min)"]),
        "penalizacion_crc": sacar_numero(crudo["Costo penalización por caja no entregada (CRC)"]),
        "cedi_id": int(sacar_numero(crudo["CEDI (ID nodo)"])),
    }


# ---- Carga cruda de las 4 hojas ----
xl = pd.ExcelFile(RUTA_EXCEL)
print("Hojas encontradas:", xl.sheet_names)

clientes_crudo = pd.read_excel(RUTA_EXCEL, sheet_name="Clientes", header=2)

distancias_crudo = pd.read_excel(RUTA_EXCEL, sheet_name="Distancias", header=None)
matriz = distancias_crudo.iloc[3:36, 1:34].reset_index(drop=True).astype(float)
matriz_np = matriz.to_numpy()

flota_crudo = pd.read_excel(RUTA_EXCEL, sheet_name="Flota", header=2)
parametros_crudo = pd.read_excel(RUTA_EXCEL, sheet_name="Parametros", header=2)

# ---- Limpieza ----
clientes = limpiar_clientes(clientes_crudo)
clientes = limpiar_ventanas(clientes)
clientes = revisar_demanda_cero(clientes)
matriz_limpia = limpiar_distancias(matriz_np)
parametros = limpiar_parametros(parametros_crudo)

print("\n--- LOG DE LIMPIEZA ---")
for linea in log_limpieza:
    print("-", linea)

print("\nParametros limpios:", parametros)