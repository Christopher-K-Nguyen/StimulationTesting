function [vtplot,buttonHandle] = getLongTermPlot(...
    channelNum,...
    chargePerPhase,...
    cycleNum,...
    time,...
    voltage,...
    current,...
    maxCathPotential_time,maxCathPotential)
%% Variables
figWidth = 960;
figHeight = 720;
windowHead = 80;
figPosX = 1;
figPosY = screenHeight - figHeight - windowHead;

%% Function
vtplot = figure(channelNum);
buttonHandle = uicontrol(...
    'Style','PushButton',...
    'String','STOP',...
    'BackgroundColor','r',...
    'Callback','delete(gcbf)');

% voltage
plot(time,voltage); hold on;
scatter(maxCathPotential_time,maxCathPotential,'rx'); hold off;
xlabel('Time (s)');
ylabel('Voltage (V)')
title_charge = sprintf('Voltage Transient at %.2f nC/ph after %g Cycles',chargePerPhase,cycleNum);
title(title_charge);
subtitle_channel = sprintf('Channel %d',channelNum);
subtitle(subtitle_channel);
xMin = round(min(time),1,'significant');
xMax = round(max(time),1,'significant');
xlim([xMin xMax]);
% max cathodal potential
Emc_text = sprintf('  \\leftarrow {\\itE}_{mc} = %.3f V',maxCathPotential);     % Emc text
Emc_annot = text(maxCathPotential_time,maxCathPotential,Emc_text,'Color','red');% Emc annotation
% current
yyaxis right
plot(time,current);
ylabel('Current (\muA)');

% Center zero
yyaxis right; ylimr = get(gca,'Ylim');ratio = ylimr(1)/ylimr(2);
yyaxis left; yliml = get(gca,'Ylim');
if yliml(2)*ratio<yliml(1)
    set(gca,'Ylim',[yliml(2)*ratio yliml(2)])
else
    set(gca,'Ylim',[yliml(1) yliml(1)/ratio])
end

set(gcf,'Position',[figPosX,figPosY,figWidth,figHeight]);
drawnow;
end