function centerFigure(fig)

figPosition = get(fig,'Position');
% figX = figPosition(1);
% figY = figPosition(2);
figWidth = figPosition(3);
figHeight = figPosition(4);

screenSize  = get(0,'ScreenSize');
% screenX = screenSize(1);
% screenY = screenSize(2);
screenWidth = screenSize(3);
screenHeight = screenSize(4);

figX_new = (screenWidth - figWidth) / 2;
figY_new = (screenHeight - figHeight) / 2;
pos_new = [figX_new figY_new figWidth figHeight];
set(fig,'Position',pos_new);

end