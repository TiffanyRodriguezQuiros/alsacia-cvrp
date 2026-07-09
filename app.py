import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from data_loader import clientes, matriz_limpia, flota_crudo, parametros, log_limpieza
from solver import (
    construir_flota_expandida,
    crear_modelo,
    agregar_funcion_objetivo,
    agregar_restriccion_capacidad,
    agregar_restriccion_tiempo,
    agregar_penalizacion_no_entrega,
    resolver,
    extraer_rutas,
)

st.set_page_config(page_title="AlSacia · Ruteo de Distribución", layout="wide")
st.title("🚚 AlSacia Distribución — Herramienta de Ruteo (CVRPTW)")

if "escenarios" not in st.session_state:
    st.session_state.escenarios = {}

# ---------------- Barra lateral: controles de escenario ----------------
with st.sidebar:
    st.header("⚙️ Escenario a simular")

    st.subheader("Flota disponible")
    unidades_editadas = {}
    for _, fila in flota_crudo.iterrows():
        unidades_editadas[fila["Tipo"]] = st.number_input(
            f"Unidades — {fila['Tipo']}",
            min_value=0, max_value=20,
            value=int(fila["Unidades_Disponibles"]), step=1,
        )

    st.subheader("Ajustar demanda de un cliente")
    nombres_clientes = clientes[clientes["ID"] != 0]["Cliente"].tolist()
    cliente_sel = st.selectbox("Cliente", nombres_clientes)
    factor_demanda = st.slider("Factor de demanda (1.0 = sin cambio)", 0.0, 3.0, 1.0, 0.1)

    nombre_escenario = st.text_input("Nombre de este escenario", value="Base")
    tiempo_limite = st.slider("Tiempo máximo de cálculo (segundos)", 5, 30, 15)
    boton_resolver = st.button("▶️ Recalcular rutas", type="primary")

# ---------------- Al presionar el botón: armar y resolver el escenario ----------------
if boton_resolver:
    clientes_escenario = clientes.copy()
    mascara = clientes_escenario["Cliente"] == cliente_sel
    clientes_escenario.loc[mascara, "Demanda_Cajas"] = (
        clientes_escenario.loc[mascara, "Demanda_Cajas"] * factor_demanda
    ).round().astype(int)

    flota_escenario = flota_crudo.copy()
    for tipo, unidades in unidades_editadas.items():
        flota_escenario.loc[flota_escenario["Tipo"] == tipo, "Unidades_Disponibles"] = unidades

    vehiculos = construir_flota_expandida(flota_escenario)
    n_nodos = len(clientes_escenario)

    manager, routing = crear_modelo(n_nodos, len(vehiculos), cedi_id=parametros["cedi_id"])
    agregar_funcion_objetivo(routing, manager, vehiculos, matriz_limpia)
    agregar_restriccion_capacidad(
        routing, manager, vehiculos, clientes_escenario["Demanda_Cajas"].to_numpy()
    )
    dim_tiempo = agregar_restriccion_tiempo(
        routing, manager, vehiculos, matriz_limpia,
        clientes_escenario["Servicio_min"].to_numpy(),
        clientes_escenario["Ventana_Ini_min"].to_numpy(),
        clientes_escenario["Ventana_Fin_min"].to_numpy(),
        parametros["jornada_max_min"],
    )
    agregar_penalizacion_no_entrega(
        routing, manager, n_nodos,
        clientes_escenario["Demanda_Cajas"].to_numpy(),
        parametros["penalizacion_crc"],
    )

    with st.spinner("Resolviendo el modelo..."):
        solucion = resolver(routing, tiempo_limite_seg=tiempo_limite)
        resultado = extraer_rutas(
            solucion, routing, manager, vehiculos, matriz_limpia, clientes_escenario, dim_tiempo
        )

    if resultado is None:
        st.error("No se encontró una solución factible con estos parámetros.")
    else:
        costo_total = sum(r["costo_total"] for r in resultado["rutas"])
        km_total = sum(r["distancia_km"] for r in resultado["rutas"])
        cajas_no_entregadas = sum(c["cajas"] for c in resultado["no_atendidos"])
        st.session_state.escenarios[nombre_escenario] = {
            "resultado": resultado,
            "clientes": clientes_escenario,
            "flota": flota_escenario,
            "costo_total": costo_total,
            "km_total": km_total,
            "cajas_no_entregadas": cajas_no_entregadas,
        }
        st.session_state.ultimo_escenario = nombre_escenario

# ---------------- Log de limpieza (siempre visible) ----------------
with st.expander("🧹 Ver hallazgos de calidad de datos y cómo se trataron"):
    for linea in log_limpieza:
        st.write("•", linea)

if not st.session_state.escenarios:
    st.info("Configurá un escenario en la barra lateral y presioná **Recalcular rutas**.")
    st.stop()

# ---------------- Tablero del último escenario ----------------
nombre_actual = st.session_state.get("ultimo_escenario", list(st.session_state.escenarios)[-1])
datos_escenario = st.session_state.escenarios[nombre_actual]
resultado = datos_escenario["resultado"]
clientes_actual = datos_escenario["clientes"]

st.subheader(f"📊 Resultados — {nombre_actual}")
sin_pedido = [c for c in resultado["no_atendidos"] if c["cajas"] == 0]
sin_atender_real = [c for c in resultado["no_atendidos"] if c["cajas"] > 0]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Costo total", f"₡{datos_escenario['costo_total']:,.0f}")
col2.metric("Vehículos usados", len(resultado["rutas"]))
col3.metric("Km totales", f"{datos_escenario['km_total']:,.1f}")
col4.metric("Clientes sin atender (pérdida real)", len(sin_atender_real))

# ---------------- Vehículos usados por tipo ----------------
st.subheader("Vehículos usados por tipo")
flota_actual = datos_escenario["flota"]
usados_por_tipo = pd.Series([r["vehiculo"] for r in resultado["rutas"]]).value_counts()
tabla_flota = pd.DataFrame([{
    "Tipo": fila["Tipo"],
    "Usados": int(usados_por_tipo.get(fila["Tipo"], 0)),
    "Disponibles": int(fila["Unidades_Disponibles"]),
    "Uso de flota": f"{(usados_por_tipo.get(fila['Tipo'], 0) / fila['Unidades_Disponibles'] * 100) if fila['Unidades_Disponibles'] > 0 else 0:.1f}%",
} for _, fila in flota_actual.iterrows()])
st.dataframe(tabla_flota, use_container_width=True, hide_index=True)

# ---------------- Mapa interactivo ----------------
colores = ["#e63946", "#457b9d", "#2a9d8f", "#f4a261", "#8338ec",
           "#ff006e", "#3a86ff", "#fb5607", "#06d6a0", "#118ab2"]

fig = go.Figure()
cedi = clientes_actual[clientes_actual["ID"] == 0].iloc[0]
fig.add_trace(go.Scattermapbox(
    lat=[cedi["Latitud"]], lon=[cedi["Longitud"]],
    mode="markers+text", text=["CEDI"], textposition="top center",
    marker=dict(size=18, color="black"),
    name="CEDI",
))

clientes_por_nombre = clientes_actual.set_index("Cliente")
for i, ruta in enumerate(resultado["rutas"]):
    sub = clientes_por_nombre.loc[ruta["clientes"]]
    fig.add_trace(go.Scattermapbox(
        lat=sub["Latitud"], lon=sub["Longitud"],
        mode="markers+lines+text",
        text=sub.index, textposition="top center",
        line=dict(width=3, color=colores[i % len(colores)]),
        marker=dict(size=9, color=colores[i % len(colores)]),
        name=f"{ruta['vehiculo']} #{i+1}",
    ))

fig.update_layout(
    mapbox=dict(style="open-street-map", zoom=7.2, center=dict(lat=9.9, lon=-84.3)),
    margin=dict(l=0, r=0, t=0, b=0), height=550,
    legend=dict(orientation="h", yanchor="bottom", y=1.01),
)
st.plotly_chart(fig, use_container_width=True)

# ---------------- Tabla detallada ----------------
st.subheader("Detalle por ruta")
tabla = pd.DataFrame([{
    "Vehículo": f"{r['vehiculo']} #{i+1}",
    "Clientes": " → ".join(r["clientes"]),
    "Km": r["distancia_km"],
    "Costo (₡)": f"₡{r['costo_total']:,.0f}",
    "Carga (cajas)": r["carga"],
    "Capacidad (cajas)": r["capacidad"],
    "% Utilización": f"{r['pct_utilizacion']:.1f}%",
} for i, r in enumerate(resultado["rutas"])])
st.dataframe(tabla, use_container_width=True, hide_index=True)

# ---------------- Clientes sin atender: distinguir "sin pedido" de "pérdida real" ----------------
if sin_pedido:
    st.info(
        "Cliente(s) sin pedido del día: "
        + ", ".join(f"{c['cliente']}" for c in sin_pedido)
    )
if sin_atender_real:
    st.warning(
        "Clientes sin atender (entrega perdida): "
        + ", ".join(f"{c['cliente']} ({c['cajas']} cajas)" for c in sin_atender_real)
    )

# ---------------- Comparación de escenarios ----------------
if len(st.session_state.escenarios) > 1:
    st.subheader("📈 Comparación de escenarios")
    comparacion = pd.DataFrame([{
        "Escenario": nombre,
        "Costo total": f"₡{e['costo_total']:,.0f}",
        "Vehículos usados": len(e["resultado"]["rutas"]),
        "Km totales": f"{e['km_total']:.1f}",
        "Cajas no entregadas": int(e["cajas_no_entregadas"]),
        "Penalización": f"₡{e['cajas_no_entregadas'] * parametros['penalizacion_crc']:,.0f}",
    } for nombre, e in st.session_state.escenarios.items()])
    st.dataframe(comparacion, use_container_width=True, hide_index=True)