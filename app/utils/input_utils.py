def limitar_entrada_usuario(texto: str, max_chars: int) -> str:
    """Recorta mensajes largos para mantener costo predecible."""
    texto = texto.strip()
    if len(texto) <= max_chars:
        return texto
    return texto[:max_chars]
