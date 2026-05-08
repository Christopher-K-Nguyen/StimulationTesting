function getPlot2(...
    time_s,...
    voltage,...
    current,...
    refElectrode)
%% Constants
FIRST_COLOR = '#0072BD';
SECOND_COLOR = '#D95319';
% THIRD_COLOR = '#EDB120';
FIG_WIDTH = 1024;
FIG_HEIGHT = 576;
WINDOW_HEADER = 60;

%% Variables
% Time
time = time_s * 1e6;

% Screen
screen = get(0,'MonitorPositions');
screen1 = screen(1,3);
screen2 = screen(2,3);
if screen1 > screen2
    screenWidth = screen(1,3);
    screenHeight = screen(1,4);
    posX = screen(1,1);
    posY = screen(1,2);
else
    screenWidth = screen(2,3);
    screenHeight = screen(2,4);
    posX = screen(2,1);
    posY = screen(2,2);
end
figPosX = ceil((screenWidth - FIG_WIDTH) / 2) + posX;
figPosY = ceil((screenHeight - FIG_HEIGHT - WINDOW_HEADER) / 2) + posY;

%% Function
fprintf('Creating plot...');
figure(1);
clf;
delete(findall(vtPlot,'type','annotation'));

% voltage
plot(time,voltage,'COLOR',FIRST_COLOR);
set(gca,'YColor','k');
yName = sprintf('Potential versus %s (V)',refElectrode);
ylabel(yName,...
    'Color',FIRST_COLOR);

yyaxis right;
plot(time,current,'Color',SECOND_COLOR);
set(gca,'YColor','k');
ylabel('Current (\muA)',...
    'Color',SECOND_COLOR,...
    'Rotation',-90,....
    'HorizontalAlignment','center',...
    'VerticalAlignment','bottom');

% Center zero
yyaxis right;
ylimR = ylim;
ylimR_big = max(abs(ylimR));
ylimR_min = -ylimR_big;
ylimR_max = ylimR_big;
ylim([ylimR_min ylimR_max]);
yyaxis left;
ylimL = ylim;
ylimL_big = max(abs(ylimL));
ylimL_min = -ylimL_big;
ylimL_max = ylimL_big;
ylim([ylimL_min ylimL_max]);

title('Pulsing');
set(gca,'XColor','k');
xlabel('Time (\mus)');
xMin = round(min(time),1,'significant');
xMax = round(max(time),1,'significant');
xlim([xMin xMax]);
set(gcf,'Position',[figPosX,figPosY,FIG_WIDTH,FIG_HEIGHT]);
set(gca,'FontSize',20);
box on;
drawnow;
fprintf('OK.\n');

end