/**
 * Shared copy for the vision-key gating (SDD#4, REV-R34).
 *
 * The three AI reprocess surfaces (GuiaDrillDown Reprocesar, ErroredGuiasPanel
 * "Reprocesar con IA", PendientesPorProcesarTab bulk button) are
 * visible-but-disabled when the capabilities store reports vision is off. They
 * all show this single, discoverable tooltip so the operator knows the action
 * is reachable once an API key is configured in Ajustes.
 */
export const VISION_DISABLED_TOOLTIP =
  'Configurá tu API key en Ajustes para habilitar la IA'
