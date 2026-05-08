function buttonHandle = getAcutePlot3(File)
%% Constants
MILLI_TO_N = 1e-3;
ANNOT_FONT = 9.5;
DIMS_FIRST = [0.2 .68 0.1 0.1];
DIMS_SECOND = [0.7 0.305 0.1 0.1];
FIRST_COLOR = '#0072BD';
SECOND_COLOR = '#D95319';
THIRD_COLOR = '#EDB120';
FOURTH_COLOR = '#4DBEEE';
RAW_SIZE = 4;
RAW_WIDTH = 3;
LINE_WIDTH = 0.5;
SCATTER_MARKER = 'x';
SCATTER_SIZE = 100;
SCATTER_WIDTH = 1.5;
SCATTER_COLOR = [0.5 0.5 0.5];
FIG_WIDTH = 1024;
FIG_HEIGHT = 576;
WINDOW_HEADER = 60;

%% Variables
stimType = File.Parameters.Type;
isMultiTest = contains2(stimType,'MULTI');
external = File.Parameters.External;
hasExternal = ~isempty(external);
refElectrode_arr = File.ReferenceElectrode.Type;
isMultiRef = iscell(refElectrode_arr);
if isMultiRef
    refElectrode = refElectrode_arr{2};
else
    refElectrode = refElectrode_arr;
end

% Index
channel_arr = [File.Data.Channel];
channelNum = length(channel_arr);
capture_arr = [File.Data(channelNum).Capture.Index];
captureNum = length(capture_arr);

% Data
chargePhase = File.Data(channelNum).Capture(captureNum).ChargePhase;
time = File.Data(channelNum).Capture(captureNum).Time;
voltage = File.Data(channelNum).Capture(captureNum).Voltage;
% current = File.Data(channelNum).Capture(captureNum).Current;
currentDensity = File.Data(channelNum).Capture(captureNum).CurrentDensity;
isAtVoltageCompliance = File.Data(channelNum).Capture(captureNum).Status.VoltageCompliance;
capacitance = File.Data(channelNum).Capture(captureNum).Capacitance;

% Waveform Data
hasVoltage = ~isempty(voltage) && any(voltage);
hasCurrent = ~isempty(currentDensity) && any(currentDensity);
% Voltag Values
if hasVoltage
    % Check
    voltage_mag = abs(voltage);
    isVoltageEmpty = all(voltage_mag < 0.005);

    % Voltage Values
    % time
    voltageValue_arr_time = File.Data(channelNum).Capture(captureNum).Figure.VoltageValues.Time;
    accessVoltage1_time = voltageValue_arr_time(1);
    drivingVoltage1_time = voltageValue_arr_time(2);
    accessVoltage2_time = voltageValue_arr_time(3);
    drivingVoltage2_time = voltageValue_arr_time(4);
    % voltage
    voltageValue_arr_voltage = File.Data(channelNum).Capture(captureNum).Figure.VoltageValues.Voltage;
    accessVoltage1 = voltageValue_arr_voltage(1);
    drivingVoltage1 = voltageValue_arr_voltage(2);
    accessVoltage2 = voltageValue_arr_voltage(3);
    drivingVoltage2 = voltageValue_arr_voltage(4);
    % plot
    voltagePlot_arr = File.Data(channelNum).Capture(captureNum).Figure.VoltageValues.Plot;
    accessVoltage1_plot = voltagePlot_arr(1);
    drivingVoltage1_plot = voltagePlot_arr(2);
    accessVoltage2_plot = voltagePlot_arr(3);
    drivingVoltage2_plot = voltagePlot_arr(4);

    % Voltage Difference
    voltageDiff_arr = File.Data(channelNum).Capture(captureNum).Figure.VoltageValues.Difference;
    voltageDiff1 = voltageDiff_arr(1);
    voltageDiff2 = voltageDiff_arr(2);

    % Access Resistance
    accessResistance_arr = File.Data(channelNum).Capture(captureNum).AccessResistance;
    accessResistance1 = accessResistance_arr(1);
    accessResistance2 = accessResistance_arr(2);

    % Max Potential Excursion
    % time
    potentialExcursion_arr_time = File.Data(channelNum).Capture(captureNum).Figure.PotentialExcursion.Time;
    potentialExcursion1_zero_time = potentialExcursion_arr_time(1);
    potentialExcursion1_time = potentialExcursion_arr_time(2);
    potentialExcursion2_zero_time = potentialExcursion_arr_time(3);
    potentialExcursion2_time = potentialExcursion_arr_time(4);
    % voltage
    potentialExcursion_arr_voltage = File.Data(channelNum).Capture(captureNum).Figure.PotentialExcursion.Voltage;
    potentialExcursion1_zero = potentialExcursion_arr_voltage(1);
    potentialExcursion1 = potentialExcursion_arr_voltage(2);
    potentialExcursion2_zero = potentialExcursion_arr_voltage(3);
    potentialExcursion2 = potentialExcursion_arr_voltage(4);
end

%% Screen
% [screenWidth,screenHeight] = get(0,'Screensize');
screen = get(0,'MonitorPositions');
[numOfScreens,~] = size(screen);
if numOfScreens > 1
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
else
    screenWidth = screen(3);
    screenHeight = screen(4);
    posX = screen(1);
    posY = screen(2);
end
figPosX = ceil((screenWidth - FIG_WIDTH) / 2) + posX;
figPosY = ceil((screenHeight - FIG_HEIGHT - WINDOW_HEADER) / 2) + posY;

%% Setup
if hasVoltage && hasCurrent
    fprintf('Creating plot with voltage and current...');
elseif hasVoltage && ~hasCurrent
    fprintf('Creating plot with voltage...');
elseif ~hasVoltage && hasCurrent
    fprintf('Creating plot with current...');
end
startTime = tic;
fig = figure(channelNum);
clf;
delete(findall(fig,'type','annotation'));
buttonHandle = uicontrol(...
    'Style','PushButton',...
    'String','STOP',...
    'BackgroundColor','r',...
    'Callback','delete(gcbf)');

%% Voltage
if hasCurrent
    yyaxis left;
end
plot(time,voltage,...
    'LineStyle','-',...
    'Color',FIRST_COLOR,...
    'LineWidth',LINE_WIDTH);
if isMultiTest
    hold on;
    working = File.Data(channelNum).Capture(captureNum).Working;
    working_mag = abs(working);
    if ~all(working_mag < 0.005)
        plot(time,working,...
            'LineStyle','--',...
            'Color',THIRD_COLOR,...
            'LineWidth',LINE_WIDTH);
    end
    if hasExternal
        counter = File.Data(channelNum).Capture(captureNum).Counter;
        counter_mag = abs(counter);
        if ~all(counter_mag < 0.005)
            plot(time,counter,...
                'LineStyle','--',...
                'Color',FOURTH_COLOR,...
                'LineWidth',LINE_WIDTH);
        end
    end
end
ax = gca;
set(ax,'YColor','k');
if isMultiTest
    yName = 'Voltage';
    color = 'black';
else
    yName = sprintf('Potential versus %s (V)',refElectrode);
    color = FIRST_COLOR;
end
ylabel(yName,'Color',color);

% Fix Left Axis
ylim('tickaligned');
yLlimL = ylim;
yLimL_big = max(abs(yLlimL));
yLimL_min = -yLimL_big;
yLimL_max = yLimL_big;
ylim([yLimL_min yLimL_max]);
yTicksL_check = yticks;
yTicksL_diff_raw = mean(abs(diff(yTicksL_check)));
yTicksL_diff = round(yTicksL_diff_raw,2,'significant');
yTicksL = yLimL_min:yTicksL_diff:yLimL_max;
if ~ismember(0,yTicksL)
    yTicksL = [yLimL_min 0 yLimL_max];
end
yticks(yTicksL);
yTicksL = yticks;
yTicksL_max = max(yTicksL);
if yTicksL_max ~= yLimL_max
    yLimL_max_new = round(yLimL_max + yTicksL_diff,2,'significant');
    yLimL_min_new = -yLimL_max_new;
    yLimL_new = [yLimL_min_new yLimL_max_new];
    yTicksL_new = yLimL_min_new:yTicksL_diff:yLimL_max_new;
    if ~ismember(0,yTicksL_new)
        yTicksL_new = [yLimL_min_new 0 yLimL_max_new];
    end
    ylim(yLimL_new);
    yticks(yTicksL_new);
end

%% Current
if hasCurrent
    yyaxis right;
    plot(time,currentDensity,...
        'LineStyle','-',...
        'Color',SECOND_COLOR,...
        'LineWidth',LINE_WIDTH, ...
        'HandleVisibility','off');
    ytickformat('%,g');
    ax = gca;
    set(ax,'YColor','k');
    ylabel('Current Density (A/cm^2)',...
        'Color',SECOND_COLOR,...
        'Rotation',-90,....
        'HorizontalAlignment','center',...
        'VerticalAlignment','bottom');

    % Fix Right Axis
    yyaxis right;
    ylim('tickaligned');
    yLimR = ylim;
    yLimR_big = max(abs(yLimR));
    yLimR_min = -yLimR_big;
    yLimR_max = yLimR_big;
    ylim([yLimR_min yLimR_max]);
    yTicksR_check = yticks;
    yTicksR_diff_raw = mean(abs(diff(yTicksR_check)));
    yTicksR_diff = round(yTicksR_diff_raw,2,'significant');
    yTicksR = yLimR_min:yTicksR_diff:yLimR_max;
    if ~ismember(0,yTicksR)
        yTicksR = [yLimR_min 0 yLimR_max];
    end
    yticks(yTicksR);
    yTicksR = yticks;
    yTicksR_max = max(yTicksR);
    if yTicksR_max ~= yLimR_max
        yLimR_max_new = round(yLimR_max + yTicksR_diff,2,'significant');
        yLimR_min_new = -yLimR_max_new;
        yLimR_new = [yLimR_min_new yLimR_max_new];
        yTicksR_new = yLimR_min_new:yTicksR_diff:yLimR_max_new;
        if ~ismember(0,yTicksR_new)
            yTicksR_new = [yLimR_min_new 0 yLimR_max_new];
        end
        ylim(yLimR_new);
        yticks(yTicksR_new);
    end
end

%% Values
if chargePhase > 0 && ~isVoltageEmpty
    if hasCurrent
        yyaxis left;
    end
    capacitance_round = round(capacitance,1);
    capacitance_use = addCommas(capacitance_round);
    capacitance_text = sprintf('{\\itC} = %s \\muF',capacitance_use);    % C text
    if ~isAtVoltageCompliance
        hold on;
        % Annotation
        % phase 1
        accessVoltage1_text = sprintf(...
            '{\\bf-{\\itV}_{acc} = %.3f V}',accessVoltage1);    % Vacc text
        accessResistance1_text = sprintf(...
            '-{\\itR}_{acc} = %.1f k\\Omega',accessResistance1);
        drivingVoltage1_text = sprintf(...
            '{\\bf-{\\itV}_{drive} = %.3f V}',drivingVoltage1); % Vdrive text
        voltageDiff1_text = sprintf(...
            '\\Delta{\\itV} = %.3f V',voltageDiff1);
        potentialExcursion1_zero_text = sprintf(...
            '{\\bf{\\itE}_{mc}({\\itt}_{pw}) = %.3f V}',...
            potentialExcursion1_zero);     % Emc text
        potentialExcursion1_text = sprintf(...
            '{\\bf{\\itE}_{mc}({\\itt}_{depol}) = %.3f V}',...
            potentialExcursion1);     % Emc text        
        if any(capacitance)
            annot1_text = sprintf('%s\n%s\n%s\n%s\n%s\n%s\n%s', ...
                accessVoltage1_text,...
                accessResistance1_text,...
                drivingVoltage1_text,...
                voltageDiff1_text,...
                potentialExcursion1_zero_text,...
                potentialExcursion1_text,...
                capacitance_text);
        else
            annot1_text = sprintf('%s\n%s\n%s\n%s\n%s\n%s', ...
                accessVoltage1_text,...
                accessResistance1_text,...
                drivingVoltage1_text,...
                voltageDiff1_text,...
                potentialExcursion1_zero_text,...
                potentialExcursion1_text);
        end
        annotation(...
            fig,...
            'textbox',DIMS_FIRST,...
            'String',annot1_text,...
            'FontSize',ANNOT_FONT,...
            'FitBoxToText','on');

        % phase 2
        accessVoltage2_text = sprintf(...
            '{\\bf+{\\itV}_{acc} = %.3f V}',accessVoltage2);    % Vacc text
        accessResistance2_text = sprintf(...
            '+{\\itR}_{acc} = %.1f k\\Omega',accessResistance2);
        drivingVoltage2_text = sprintf(...
            '{\\bf+{\\itV}_{drive} = %.3f V}',drivingVoltage2); % Vdrive text
        voltageDiff2_text = sprintf(...
            '\\Delta{\\itV} = %.3f V',voltageDiff2);
        potentialExcursion2_zero_text = sprintf(...
            '{\\bf{\\itE}_{ma}({\\itt}_{pw}) = %.3f V}',...
            potentialExcursion2_zero);     % Emc text
        potentialExcursion2_text = sprintf(...
            '{\\bf{\\itE}_{ma}({\\itt}_{depol}) = %.3f V}',...
            potentialExcursion2);     % Emc text
        annot2_text = sprintf('%s\n%s\n%s\n%s\n%s\n%s', ...
            accessVoltage2_text,...
            accessResistance2_text,...
            drivingVoltage2_text,...
            voltageDiff2_text,...
            potentialExcursion2_zero_text,...
            potentialExcursion2_text);
        annotation(...
            fig,...
            'textbox',DIMS_SECOND,...
            'String',annot2_text,...
            'FontSize',ANNOT_FONT,...
            'FitBoxToText','on');

        % Scatter
        figure(fig);
        % phase 1
        scatter1_time = [...
            accessVoltage1_time,...
            drivingVoltage1_time,...
            potentialExcursion1_zero_time,...
            potentialExcursion1_time];
        scatter1_voltage = [...
            accessVoltage1_plot,...
            drivingVoltage1_plot,...
            potentialExcursion1_zero,...
            potentialExcursion1];
        % phase 2
        scatter2_time = [...
            accessVoltage2_time,...
            drivingVoltage2_time,...
            potentialExcursion2_zero_time,...
            potentialExcursion2_time];
        scatter2_voltage = [...
            accessVoltage2_plot,...
            drivingVoltage2_plot,...
            potentialExcursion2_zero,...
            potentialExcursion2];
        % combine
        scatter_time = [scatter1_time,scatter2_time];
        scatter_voltage = [scatter1_voltage,scatter2_voltage];
        scatter(scatter_time,scatter_voltage,...
            'Marker',SCATTER_MARKER,...
            'SizeData',SCATTER_SIZE,...
            'LineWidth',SCATTER_WIDTH,...
            'MarkerEdgeColor',SCATTER_COLOR,...
            'HandleVisibility','off');
    else
        annotation(...
            fig,...
            'textbox',DIMS_FIRST,...
            'String',capacitance_text,...
            'FontSize',20,...
            'FitBoxToText','on');
    end
end

%% Labels
if channelNum ~= 0
    title_channel = sprintf('Channel %d',channelNum);
    %     if chargePhase ~= 0
    %         amplitude1_mag = abs(amplitude);
    %         subtitle_charge = sprintf('{\\iti}_{mag} = %.1f \\muA; {\\itQ}_{ph} = %.4f nC/ph',amplitude1_mag,chargePhase);
    %     else
    %         subtitle_charge = sprintf('{\\iti}_{mag} = 0.0 \\muA; {\\itQ}_{ph} = 0.0000 nC/ph');
    %     end
    %     subtitle(subtitle_charge);
else
    title_channel = 'Pulsing';
end
title(title_channel);
set(ax,'XColor','k');
xlabel('Time (\mus)');
% numOfDigits = ceil(log10(max(time)));
% scale = 10^(numOfDigits-1);
% xMin_round = ceil(min(time));
% xMax_round = floor(max(time));
% xMin_rem = rem(xMin_round,scale);
% xMax_rem = rem(xMax_round,scale);
% xMin = xMin_round - xMin_rem;
% xMax = xMax_round - xMax_rem;
xMin = round(min(time),1,'significant');
xMax = round(max(time),1,'significant');
xlim([xMin xMax]);
set(fig,'Position',[figPosX,figPosY,FIG_WIDTH,FIG_HEIGHT]);
set(ax,'FontSize',20);
box on;
if isMultiTest
    voltage_legend = sprintf('Potential versus %s',refElectrode);
    working_legend = 'Potential versus Ag|AgCl';
    if hasExternal
        counter_legend = sprintf('%s versus Ag|AgCl',refElectrode);
        legend_cell = {voltage_legend,working_legend,counter_legend};
    else
        legend_cell = {voltage_legend,working_legend};
    end
    legend(legend_cell,'Location','best');
end
% drawnow;
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

end