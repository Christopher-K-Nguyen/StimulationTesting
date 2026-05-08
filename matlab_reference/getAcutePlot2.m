function buttonHandle = getAcutePlot2(...
    channelNum,...
    amplitude,...
    chargePhase,...
    time,...
    voltage,...
    current,...
    voltage_arr,...
    voltageDiff_arr,...
    potentialExcursion_mat,...
    refElectrode)
%% Constants
COMPLIANCE_THRESH = 10;  % threshold for compliance
DIMS_NEG = [.2 .68 .1 .1];
DIMS_POS = [.7 .28 .1 .1];
FIRST_COLOR = '#0072BD';
SECOND_COLOR = '#D95319';
% THIRD_COLOR = '#EDB120';
FIG_WIDTH = 1024;
FIG_HEIGHT = 576;
WINDOW_HEADER = 60;

%% Variables
hasVoltage = ~isempty(voltage) && any(voltage);
hasCurrent = ~isempty(current) && any(current);
% Voltag Values
if hasVoltage
    % Check
    voltage_mag = abs(voltage);
    isVoltageEmpty = ~any(voltage_mag > 0.1);

    % Compliance
    voltage_max = max(abs(voltage));
    isAtVoltageCompliance = voltage_max> COMPLIANCE_THRESH;

    % Values
    voltage_arr_time = voltage_arr(1,:);
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
    drivingVoltage2_plot = voltage_arr_voltage(6);

    % Voltage Difference
    voltageDiff1 = voltageDiff_arr(1);
    voltageDiff2 = voltageDiff_arr(2);

    % Max Potential Excursion
    potentialExcursion_arr_time = potentialExcursion_mat(1,:);
    potentialExcursion1_zero_time = potentialExcursion_arr_time(1);
    potentialExcursion1_time = potentialExcursion_arr_time(2);
    potentialExcursion2_zero_time = potentialExcursion_arr_time(3);
    potentialExcursion2_time = potentialExcursion_arr_time(4);

    potentialExcursion_arr_voltage = potentialExcursion_mat(2,:);
    potentialExcursion1_zero = potentialExcursion_arr_voltage(1);
    potentialExcursion1 = potentialExcursion_arr_voltage(2);
    potentialExcursion2_zero = potentialExcursion_arr_voltage(3);
    potentialExcursion2 = potentialExcursion_arr_voltage(4);
end

% Screen
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

%% Function
if hasVoltage && hasCurrent
   fprintf('Creating plot with voltage and current...');
elseif hasVoltage && ~hasCurrent
   fprintf('Creating plot with voltage...');
elseif ~hasVoltage && hasCurrent
   fprintf('Creating plot with current...');
end
fig = figure(channelNum);
clf;
delete(findall(fig,'type','annotation'));
buttonHandle = uicontrol(...
    'Style','PushButton',...
    'String','STOP',...
    'BackgroundColor','r',...
    'Callback','delete(gcbf)');

% voltage
if hasVoltage
    if hasCurrent
        yyaxis left;
    end
    plot(time,voltage,'COLOR',FIRST_COLOR);
    ax = gca;
    set(ax,'YColor','k');
    yName = sprintf('Potential versus %s (V)',refElectrode);
    ylabel(yName,...
        'Color',FIRST_COLOR);
    if chargePhase ~= 0 && ~isAtVoltageCompliance && ~isVoltageEmpty
        hold on;
        accessVoltage1_text = sprintf(...
            '{\\bf-{\\itV}_{acc} = %.3f V}',accessVoltage1);    % Vacc text
        drivingVoltage1_text = sprintf(...
            '{\\bf-{\\itV}_{drive} = %.3f V}',drivingVoltage1); % Vdrive text
        voltageDiff1_text = sprintf(...
            '\\Delta{\\itV} = %.3f V',...
            voltageDiff1);     % Emc text
        potentialExcursion1_zero_text = sprintf(...
            '{\\bf{\\itE}_{mc}({\\itt}_{pw}) = %.3f V}',...
            potentialExcursion1_zero);     % Emc text
        potentialExcursion1_text = sprintf(...
            '{\\bf{\\itE}_{mc}({\\itt}_{depol}) = %.3f V}',...
            potentialExcursion1);     % Emc text
        annot1_text = sprintf('%s\n%s\n%s\n%s\n%s', ...
            accessVoltage1_text,...
            drivingVoltage1_text,...
            voltageDiff1_text,...
            potentialExcursion1_zero_text,...
            potentialExcursion1_text);
        annotation(...
            fig,...
            'textbox',DIMS_NEG,...
            'String',annot1_text,...
            'FontSize',10,...
            'FitBoxToText','on');

        scatter1_time = [...
            accessVoltage1_time,...
            drivingVoltage1_time,...
            potentialExcursion1_zero_time,...
            potentialExcursion1_time];
        scatter1_voltage = [...
            -accessVoltage1,...
            -drivingVoltage1,...
            potentialExcursion1_zero,...
            potentialExcursion1];
        scatter(scatter1_time,scatter1_voltage,...
            'Marker','*',...
            'MarkerEdgeColor','k');

        accessVoltage2_text = sprintf(...
            '{\\bf+{\\itV}_{acc} = %.3f V}',accessVoltage2);    % Vacc text
        drivingVoltage2_text = sprintf(...
            '{\\bf+{\\itV}_{drive} = %.3f V}',drivingVoltage2); % Vdrive text
        voltageDiff2_text = sprintf(...
            '\\Delta{\\itV} = %.3f V',...
            voltageDiff2);     % Emc text
        potentialExcursion2_zero_text = sprintf(...
            '{\\bf{\\itE}_{ma}({\\itt}_{pw}) = %.3f V}',...
            potentialExcursion2_zero);     % Emc text
        potentialExcursion2_text = sprintf(...
            '{\\bf{\\itE}_{ma}({\\itt}_{depol}) = %.3f V}',...
            potentialExcursion2);     % Emc text
        annot2_text = sprintf('%s\n%s\n%s\n%s\n%s', ...
            accessVoltage2_text,...
            drivingVoltage2_text,...
            voltageDiff2_text,...
            potentialExcursion2_zero_text,...
            potentialExcursion2_text);
        figure(channelNum);
        annotation(...
            fig,...
            'textbox',DIMS_POS,...
            'String',annot2_text,...
            'FontSize',10,...
            'FitBoxToText','on');

        figure(channelNum);
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
        scatter(scatter2_time,scatter2_voltage,...
            'Marker','*',...
            'MarkerEdgeColor','k');
    end

    % Fix Left Axis
    ylim('tickaligned');
    yLlimL = ylim;
    yLimL_big = max(abs(yLlimL));
    yLimL_min = -yLimL_big;
    yLimL_max = yLimL_big;
    ylim([yLimL_min yLimL_max]);
    yTicksL_check = yticks;
    yTicksL_diff = round(mean(abs(diff(yTicksL_check))),2,'significant');
    yTicksL = [yLimL_min:yTicksL_diff:yLimL_max];
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

% current
if hasCurrent
    yyaxis right;
    color = SECOND_COLOR;
    plot(time,current,'Color',color);
    ax = gca;
    set(ax,'YColor','k');
    ylabel('Current (\muA)',...
        'Color',color,...
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
    yTicksR_diff = round(mean(abs(diff(yTicksR_check))),2,'significant');
    yTicksR = [yLimR_min:yTicksR_diff:yLimR_max];
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
drawnow;
% pause(1);
fprintf('OK\n');

end