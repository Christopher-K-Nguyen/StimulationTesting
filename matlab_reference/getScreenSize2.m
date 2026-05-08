function [...
    screenX,screenY,...
    screenWidth,screenHeight,...
    numOfMonitors]...
    = getScreenSize2()

screenSize = get(0,'monitorpositions');
[numOfMonitors,~] = size(screenSize);
screenX = screenSize(:,1);
screenY = screenSize(:,2);
screenWidth = screenSize(:,3);
screenHeight = screenSize(:,4);

end