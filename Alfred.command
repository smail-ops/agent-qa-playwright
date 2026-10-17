#!/bin/zsh
# Lance Alfred d'un double-clic (macOS).
# Garde cette fenêtre ouverte pendant que tu utilises Alfred : la fermer, ou faire Ctrl + C, l'arrête.

cd "${0:A:h}" || exit 1  # le dossier du projet, là où se trouve ce fichier

if [[ ! -x .venv/bin/python ]]; then
  echo "Environnement .venv introuvable dans ce dossier : suis d'abord l'installation du README."
  read -k1 "?Appuie sur une touche pour fermer."
  exit 1
fi

exec .venv/bin/python -m alfred
