function [vtPlot,buttonHandle] = getAcutePlot(...
    channelNum,...
    amplitude1,...
    chargePhase,...
    time,...
    voltage,...
    current,...
    voltage_arr,...
    voltageDiff_arr,...
    maxPotential_arr,...
    refElectrode,...
    isAtVoltageCompliance)
%% Constants
DIMS_NEG = [.2 .68 .1 .1];
DIMS_POS = [.7 .28 .1 .1];
FIRST_COLOR = '#0072BD';
SECOND_COLOR = '#D95319';
% THIRD_COLOR = '#EDB120';
FIG_WIDTH = 1024;
FIG_HEIGHT = 576;
WINDOW_HEADER = 60;

%% Variables
time = time * 1e6;
% Voltag Values
if ~isempty(voltage)
    voltage_arr_time = voltage_arr(1,:) * 1e6;
    accessVoltage1_time = voltage_arr_time(1);
    drivingVoltage1_time = voltage_arr_time(2);
    accessVoltage2_time = voltage_arr_time(3);
    drivingVoltage2_time = voltage_arr_time(5);
    
    voltage_arr_voltage = voltage_arr(2,:);
    accessVoltage1 = voltage_arr_voltage(1);
    drivingVoltage1 = voltage_arr_voltage(2);
    accessVoltage2 = voltage_arr_voltage(3);
    accessVoltage2_plot = voltage_arr_voltage(4);
    drivingVoltage2 = voltage_arr_voltage(5);
    
    % Voltage Difference
    voltageDiff1 = voltageDiff_arr(1);
    voltageDiff2 = voltageDiff_arr(2);
    
    % Max Potential Excursion
    maxPotential_arr_time = maxPotential_arr(1,:) * 1e6;
    maxPotential1_zero_time = maxPotential_arr_time(1);
    maxPotential1_time = maxPotential_arr_time(2);
    maxPotential2_zero_time = maxPotential_arr_time(3);
    maxPotential2_time = maxPotential_arr_time(4);
    
    maxPotential_arr_voltage = maxPotential_arr(2,:);
    maxPotential1_zero = maxPotential_arr_voltage(1);
    maxPotential1 = maxPotential_arr_voltage(2);
    maxPotential2_zero = maxPotential_arr_voltage(3);
    maxPotential2 = maxPotential_arr_voltage(4);
end

% Screen
% [screenWidth,screenHeight] = get(0,'Screensize');
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
if channelNum ~= 0
    vtPlot = figure(channelNum);
else
    fig_arr =  findobj('type','figure');
    figNum = length(fig_arr) + 1;
    vtPlot = figure(figNum);
end
clf;
delete(findall(vtPlot,'type','annotation'));
buttonHandle = uicontrol(...
    'Style','PushButton',...
    'String','STOP',...
    'BackgroundColor','r',...
    'Callback','delete(gcbf)');

% voltage
if ~isempty(voltage)
    if ~isempty(current)
        yyaxis left;
    end
    plot(time,voltage,'COLOR',FIRST_COLOR);
    set(gca,'YColor','k');
    yName = sprintf('Potential versus %s (V)',refElectrode);
    ylabel(yName,...
        'Color',FIRST_COLOR);
    if ~isAtVoltageCompliance
        if chargePhase ~= 0
            hold on;
            accessVoltage1_text = sprintf(...
                '{\\bf-{\\itV}_{acc} = %.3f V}',accessVoltage1);    % Vacc text
            drivingVoltage1_text = sprintf(...
                '{\\bf-{\\itV}_{drive} = %.3f V}',drivingVoltage1); % Vdrive text
            voltageDiff1_text = sprintf(...
                '\\Delta{\\itV} = %.3f V',...
                voltageDiff1);     % Emc text
            maxPotential1_zero_text = sprintf(...
                '{\\bf{\\itE}_{mc}({\\itt}_{pw}) = %.3f V}',...
                maxPotential1_zero);     % Emc text
            maxPotential1_text = sprintf(...
                '{\\bf{\\itE}_{mc}({\\itt}_{depol}) = %.3f V}',...
                maxPotential1);     % Emc text
            annot1_text = sprintf('%s\n%s\n%s\n%s\n%s', ...
                accessVoltage1_text,...
                drivingVoltage1_text,...
                voltageDiff1_text,...
                maxPotential1_zero_text,...
                maxPotential1_text);
            annotation(...
                vtPlot,...
                'textbox',DIMS_NEG,...
                'String',annot1_text,...
                'FontSize',10,...
                'FitBoxToText','on');

            scatter1_time = [...
                accessVoltage1_time,...
                drivingVoltage1_time,...
                maxPotential1_zero_time,...
                maxPotential1_time];
            scatter1_voltage = [...
                -accessVoltage1,...
                -drivingVoltage1,...
                maxPotential1_zero,...
                maxPotential1];
            scatter(scatter1_time,scatter1_voltage,...
                'Marker','.',...
                'MarkerEdgeColor','k');

            accessVoltage2_text = sprintf(...
                '{\\bf+{\\itV}_{acc} = %.3f V}',accessVoltage2);    % Vacc text
            drivingVoltage2_text = sprintf(...
                '{\\bf+{\\itV}_{drive} = %.3f V}',drivingVoltage2); % Vdrive text
            voltageDiff2_text = sprintf(...
                '\\Delta{\\itV} = %.3f V',...
                voltageDiff2);     % Emc text
            maxPotential2_zero_text = sprintf(...
                '{\\bf{\\itE}_{ma}({\\itt}_{pw}) = %.3f V}',...
                maxPotential2_zero);     % Emc text
            maxPotential2_text = sprintf(...
                '{\\bf{\\itE}_{ma}({\\itt}_{depol}) = %.3f V}',...
                maxPotential2);     % Emc text
            annot2_text = sprintf('%s\n%s\n%s\n%s\n%s', ...
                accessVoltage2_text,...
                drivingVoltage2_text,...
                voltageDiff2_text,...
                maxPotential2_zero_text,...
                maxPotential2_text);
            figure(channelNum);
            annotation(...
                vtPlot,...
                'textbox',DIMS_POS,...
                'String',annot2_text,...
                'FontSize',10,...
                'FitBoxToText','on');

            figure(channelNum);
            scatter2_time = [...
                accessVoltage2_time,...
                drivingVoltage2_time,...
                maxPotential2_zero_time,...
                maxPotential2_time];
            scatter2_voltage = [...
                accessVoltage2_plot,...
                drivingVoltage2,...
                maxPotential2_zero,...
                maxPotential2];
            scatter(scatter2_time,scatter2_voltage,...
                'Marker','.',...
                'MarkerEdgeColor','k');
        end
    end
end

% current
if ~isempty(current)
    if ~isempty(voltage)
        yyaxis right;
        color = SECOND_COLOR;
    else
        color = FIRST_COLOR;
    end
    plot(time,current,'Color',color);
    set(gca,'YColor','k');
    ylabel('Current (\muA)',...
        'Color',color,...
        'Rotation',-90,....
        'HorizontalAlignment','center',...
        'VerticalAlignment','bottom');

    % Center zero
    if ~isempty(current) && ~isempty(voltage)
        % Fix Right Axis
        yyaxis right;
        ylimR = ylim;
        ylimR_big = max(abs(ylimR));
        ylimR_min = -ylimR_big;
        ylimR_max = ylimR_big;
        ylim([ylimR_min ylimR_max]);
    
        % Fix Left Axis
        yyaxis left;
        ylimL = ylim;
        ylimL_big = max(abs(ylimL));
        ylimL_min = -ylimL_big;
        ylimL_max = ylimL_big;
        ylim([ylimL_min ylimL_max]);
    end
else
    yBounds_mag = abs(ylim);
    yBounds_mag_max = max(yBounds_mag);
    ylim([-yBounds_mag_max yBounds_mag_max]);
end

if channelNum ~= 0
    if isAtVoltageCompliance
        title_channel = sprintf('Channel %d (BAD)',channelNum);
    else
        title_channel = sprintf('Channel %d',channelNum);
    end

    if chargePhase ~= 0
        amplitude1_mag = abs(amplitude1);
        subtitle_charge = sprintf('{\\iti}_{mag} = %.1f \\muA; {\\itQ}_{ph} = %.2f nC/ph',amplitude1_mag,chargePhase);
    else
        subtitle_charge = sprintf('{\\iti}_{mag} = 0.0 \\muA; {\\itQ}_{ph} = 0.00 nC/ph');
    end
    subtitle(subtitle_charge);
else
    title_channel = 'Pulsing';
end
title(title_channel);

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