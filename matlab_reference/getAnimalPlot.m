function [vtPlot] = getAnimalPlot(...
    channelNum,...
    chargePerPhase,...
    time,...
    voltage,...
    current,...
    openCircuit,...
    maxPotential,...
    voltageDrive,...
    isBroken)
%% Constants
FIRST_COLOR = '#0072BD';
SECOND_COLOR = '#D95319';
THIRD_COLOR = '#EDB120';

%% Variables
time = time / 1e-6;
% [screenWidth,screenHeight] = get(0,'Screensize');
screen = get(0,'MonitorPositions');
screenWidth = screen(1,3);
screenHeight = screen(1,4);
figWidth = 800;
figHeight = 600;
windowHead = 60;
figPosX = ceil((screenWidth - figWidth) / 2);
figPosY = ceil((screenHeight - figHeight - windowHead) / 2);


%% Function
fprintf('Creating plot...');

vtPlot = figure(channelNum);
clf;
delete(findall(gcf,'type','annotation'));

% voltage
if ~isempty(voltage)
    if ~isempty(current)
        yyaxis left;
    end
    plot(time,voltage,'COLOR',FIRST_COLOR);
    set(gca,'YColor','k');
    ylabel('Voltage (V)',...
        'Color',FIRST_COLOR);
    if ~isBroken
        % max potential
        if maxPotential < 0
            lim_text = 'mc';
        else
            lim_text = 'ma';
        end
        openCircuit_text = sprintf('{\\itE}_{oc} = %.3f V',openCircuit);     % Emc text
        maxPotential_text = sprintf('{\\itE}_{%s} = %.3f V',lim_text,maxPotential);     % Emc text
        voltageDrive_text = sprintf('{\\itV}_{drive} = %.3f V',voltageDrive);     % Emc text
        annot_text = sprintf('%s\n%s\n%s',openCircuit_text,maxPotential_text,voltageDrive_text);
        annotation(...
            vtPlot,...
            'textbox',[.18 .7 .1 .1],...
            'String',annot_text,...
            'FontSize',16,...
            'FitBoxToText','on');
    end
end

% current
if ~isempty(current)
    yyaxis right;
    plot(time,current,'Color',SECOND_COLOR);
    set(gca,'YColor','k');
    ylabel('Current (\muA)',...
        'Color',SECOND_COLOR,...
        'Rotation',-90,....
        'HorizontalAlignment','center',...
        'VerticalAlignment','bottom');

    % Center zero
    %     yyaxis left;
    %     ylim('tickaligned');
    yyaxis right;
    ylimR = ylim;
%     ylimR_min = ylimR(1);
%     ylimR_max = ylimR(2);
    ylimR_big = max(abs(ylimR));
    ylimR_min = -ylimR_big;
    ylimR_max = ylimR_big;
    ylim([ylimR_min ylimR_max]);
%     ratioR = ylimR_min / ylimR_max;
    %     ylim('tickaligned');
    yyaxis left;
    ylimL = ylim;
    %     ylimL_min = ylimL(1);
    %     ylimL_max = ylimL(2);
    ylimL_big = max(abs(ylimL));
    ylimL_min = -ylimL_big;
    ylimL_max = ylimL_big;
    ylim([ylimL_min ylimL_max]);
%     ylimL_max_fix = ylimL_max * ratioR;
%     if ylimL_max_fix < ylimL_min
%         set(gca,'Ylim',[ylimL_max_fix ylimL_max])
%     else
%         ylimL_max_alt = ylimL_min / ratioR;
%         set(gca,'Ylim',[ylimL_min ylimL_max_alt])
%     end
end

title_charge = sprintf('Voltage Transient at %.2f nC/ph',chargePerPhase);
title(title_charge);

if isBroken
    subtitle_channel = sprintf('Channel %d (Broken)',channelNum);
else
    subtitle_channel = sprintf('Channel %d',channelNum);
end
subtitle(subtitle_channel);
set(gca,'XColor','k');
xlabel('Time (\mus)');
xMin = round(min(time),1,'significant');
xMax = round(max(time),1,'significant');
xlim([xMin xMax]);
set(gcf,'Position',[figPosX,figPosY,figWidth,figHeight]);
set(gca,'FontSize',20);
box on;
drawnow;
fprintf('OK.\n');

end