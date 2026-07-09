from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp


def construir_flota_expandida(flota_df):
    vehiculos = []
    for _, fila in flota_df.iterrows():
        for _ in range(int(fila["Unidades_Disponibles"])):
            vehiculos.append({
                "tipo": fila["Tipo"],
                "capacidad": int(fila["Capacidad_Cajas"]),
                "costo_fijo": float(fila["Costo_Fijo_CRC"]),
                "costo_km": float(fila["Costo_km_CRC"]),
                "velocidad_kmh": float(fila["Vel_Prom_kmh"]),
            })
    return vehiculos


def crear_modelo(n_nodos, n_vehiculos, cedi_id=0):
    manager = pywrapcp.RoutingIndexManager(n_nodos, n_vehiculos, cedi_id)
    routing = pywrapcp.RoutingModel(manager)
    return manager, routing


def agregar_funcion_objetivo(routing, manager, vehiculos, distancias):
    """
    Costo por km (c_k * d_ij * x_ijk) + costo fijo por vehículo usado (F_k * y_k).
    """
    for k, v in enumerate(vehiculos):
        def callback_costo(from_index, to_index, v=v):
            i = manager.IndexToNode(from_index)
            j = manager.IndexToNode(to_index)
            return int(distancias[i, j] * v["costo_km"])

        idx_callback = routing.RegisterTransitCallback(callback_costo)
        routing.SetArcCostEvaluatorOfVehicle(idx_callback, k)
        routing.SetFixedCostOfVehicle(int(v["costo_fijo"]), k)


def agregar_restriccion_capacidad(routing, manager, vehiculos, demandas):
    """
    sum(demanda de clientes asignados) <= capacidad del vehículo k.
    """
    def callback_demanda(from_index):
        nodo = manager.IndexToNode(from_index)
        return int(demandas[nodo])

    idx_demanda = routing.RegisterUnaryTransitCallback(callback_demanda)
    routing.AddDimensionWithVehicleCapacity(
        idx_demanda,
        0,
        [v["capacidad"] for v in vehiculos],
        True,
        "Capacidad",
    )


def agregar_restriccion_tiempo(routing, manager, vehiculos, distancias, servicios,
                                ventana_ini, ventana_fin, jornada_max_min):
    """
    Tiempo de viaje (según velocidad de cada vehículo) + tiempo de servicio,
    respetando ventana de tiempo por cliente y jornada máxima por vehículo.
    """
    indices_tiempo = []
    for k, v in enumerate(vehiculos):
        def callback_tiempo(from_index, to_index, v=v):
            i = manager.IndexToNode(from_index)
            j = manager.IndexToNode(to_index)
            minutos_viaje = (distancias[i, j] / v["velocidad_kmh"]) * 60
            return int(minutos_viaje + servicios[i])

        idx_t = routing.RegisterTransitCallback(callback_tiempo)
        indices_tiempo.append(idx_t)

    routing.AddDimensionWithVehicleTransits(
        indices_tiempo,
        int(jornada_max_min),  # espera máxima permitida
        int(jornada_max_min),  # jornada máxima por vehículo
        False,
        "Tiempo",
    )
    dimension_tiempo = routing.GetDimensionOrDie("Tiempo")

    for nodo in range(len(ventana_ini)):
        index = manager.NodeToIndex(nodo)
        dimension_tiempo.CumulVar(index).SetRange(
            int(ventana_ini[nodo]), int(ventana_fin[nodo])
        )

    return dimension_tiempo


def agregar_penalizacion_no_entrega(routing, manager, n_nodos, demandas, penalizacion_por_caja):
    """
    Variable z_i: permite dejar un cliente sin atender pagando P * cajas.
    """
    for nodo in range(1, n_nodos):  # todos menos el CEDI (nodo 0)
        index = manager.NodeToIndex(nodo)
        penalizacion = int(demandas[nodo] * penalizacion_por_caja)
        routing.AddDisjunction([index], penalizacion)


def resolver(routing, tiempo_limite_seg=15):
    """
    Heurística de construcción (PATH_CHEAPEST_ARC) + metaheurística de
    mejora (GUIDED_LOCAL_SEARCH). Necesario porque el CVRPTW es NP-difícil:
    no hay forma exacta de resolverlo en tiempo razonable para 33 nodos.
    """
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.FromSeconds(tiempo_limite_seg)
    return routing.SolveWithParameters(params)


def extraer_rutas(solucion, routing, manager, vehiculos, distancias, clientes_df, dimension_tiempo):
    if solucion is None:
        return None

    rutas = []
    for k in range(len(vehiculos)):
        index = routing.Start(k)
        if routing.IsEnd(solucion.Value(routing.NextVar(index))):
            continue  # vehículo no usado

        secuencia = []
        distancia_ruta = 0
        while not routing.IsEnd(index):
            nodo = manager.IndexToNode(index)
            secuencia.append(nodo)
            siguiente = solucion.Value(routing.NextVar(index))
            nodo_sig = manager.IndexToNode(siguiente)
            distancia_ruta += distancias[nodo, nodo_sig]
            index = siguiente
        secuencia.append(manager.IndexToNode(index))  # vuelta al CEDI

        v = vehiculos[k]
        costo_km = distancia_ruta * v["costo_km"]

        # --- Carga: suma de la demanda de todos los clientes visitados en esta ruta ---
        carga = int(sum(clientes_df.iloc[n]["Demanda_Cajas"] for n in secuencia))
        capacidad = v["capacidad"]
        pct_utilizacion = round(100 * carga / capacidad, 1) if capacidad > 0 else 0

        rutas.append({
            "vehiculo": v["tipo"],
            "clientes": [clientes_df.iloc[n]["Cliente"] for n in secuencia],
            "distancia_km": round(distancia_ruta, 1),
            "costo_total": round(v["costo_fijo"] + costo_km, 0),
            "carga": carga,
            "capacidad": capacidad,
            "pct_utilizacion": pct_utilizacion,
        })

    # --- Clientes sin atender, con su demanda para distinguir "sin pedido" de "entrega perdida" ---
    no_atendidos = []
    for nodo in range(1, len(clientes_df)):
        index = manager.NodeToIndex(nodo)
        if solucion.Value(routing.NextVar(index)) == index:
            no_atendidos.append({
                "cliente": clientes_df.iloc[nodo]["Cliente"],
                "cajas": int(clientes_df.iloc[nodo]["Demanda_Cajas"]),
            })

    return {"rutas": rutas, "no_atendidos": no_atendidos}


if __name__ == "__main__":
    from data_loader import clientes, matriz_limpia, flota_crudo, parametros

    vehiculos = construir_flota_expandida(flota_crudo)
    n_nodos = len(clientes)

    manager, routing = crear_modelo(n_nodos, len(vehiculos), cedi_id=parametros["cedi_id"])

    agregar_funcion_objetivo(routing, manager, vehiculos, matriz_limpia)
    agregar_restriccion_capacidad(routing, manager, vehiculos, clientes["Demanda_Cajas"].to_numpy())
    dim_tiempo = agregar_restriccion_tiempo(
        routing, manager, vehiculos, matriz_limpia,
        clientes["Servicio_min"].to_numpy(),
        clientes["Ventana_Ini_min"].to_numpy(),
        clientes["Ventana_Fin_min"].to_numpy(),
        parametros["jornada_max_min"],
    )
    agregar_penalizacion_no_entrega(
        routing, manager, n_nodos,
        clientes["Demanda_Cajas"].to_numpy(),
        parametros["penalizacion_crc"],
    )

    solucion = resolver(routing, tiempo_limite_seg=15)
    resultado = extraer_rutas(solucion, routing, manager, vehiculos, matriz_limpia, clientes, dim_tiempo)

    if resultado is None:
        print("No se encontró solución factible.")
    else:
        for r in resultado["rutas"]:
            print(f"{r['vehiculo']}: {' -> '.join(r['clientes'])} | {r['distancia_km']} km | costo {r['costo_total']:,.0f} | carga {r['carga']}/{r['capacidad']} ({r['pct_utilizacion']}%)")
        print("No atendidos:", [(c["cliente"], c["cajas"]) for c in resultado["no_atendidos"]])