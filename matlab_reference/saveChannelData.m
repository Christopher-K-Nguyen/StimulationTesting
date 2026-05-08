function [] = saveChannelData(...
    chargePhase,...
    name,...
    channelNum,...
    savePath,...
    structVT,...
    vtPlot)
%% Constants
N_TO_MICRO = 1e6;
% ULTRA_AREA = 500;

%% Variables
fields = fieldnames(structVT);
hasArea = any(contains(fields,'SurfaceArea','IgnoreCase',true));

%% Function
fprintf('Saving files...\n');

% Starting filename
channelName = sprintf('CH%02d',channelNum);
if isinf(chargePhase)
    filename_char = sprintf('%s_%s_Max',...    % Emc
    name,...
    channelName);                                  % formatted variables
else
    filename_char = sprintf('%s_%s_%03dnC',... % charge per phase
    name,channelName,chargePhase);                % formatted variables
end

% Capture data of channel
time_data = structVT(channelNum).Time * N_TO_MICRO;              % time data array
voltage_data = structVT(channelNum).VoltageTransient;            % voltage data array
current_data = structVT(channelNum).CurrentStimulation;          % current data array
chargePhase_data = structVT(channelNum).ChargePhase;      	% charge per phase value
if hasArea
    geomSurfaceArea_data = structVT(channelNum).GeometricSurfaceArea;% geometric surface area
end
chargeInj_data = structVT(channelNum).ChargeInjection;           % charge injection value
maxPotential_data = structVT(channelNum).MaxPotentialExcursion;   % maximum cathodal potential value
voltageDrive_data = structVT(channelNum).DrivingVoltage;           % driving voltage value
dateTime_data = structVT(channelNum).DateTime;                   % date and time completed
status_data = structVT(channelNum).Status;

% Arrays for single values
blank_len = length(time_data);          % length of data
blank_array = strings(blank_len,1);	% array of blank space
chargePhase_strings = blank_array;
chargePhase_strings(1) = string(chargePhase_data);     	% add charge per phase to array
if hasArea
    geomSurfaceArea_strings = blank_array;
    geomSurfaceArea_strings(1) = string(geomSurfaceArea_data);  % add charge per phase to array
end
chargeInj_strings = blank_array;
chargeInj_strings(1) = string(chargeInj_data);          % add charge injection to array
maxPotential_strings = blank_array;
maxPotential_strings(1) = string(maxPotential_data);        % add max potential to array
voltageDrive_strings = blank_array;
voltageDrive_strings(1) = string(voltageDrive_data);        % add driving voltage to array
dateTime_strings = blank_array;
dateTime_strings(1) = dateTime_data;                        % add date and time to array
status_strings = blank_array;
status_strings(1) = status_data;

% Table
if all(maxPotential_data < 0)
    maxPotentialTitle = 'Emc (V)';
else
    maxPotentialTitle = 'Ema (V)';
end
if hasArea
    tableHeading = {...     % heading
        'Time (us)',...     % time
        'Voltage (V)',...   % voltage transient
        'Current (uA)',...  % current stimulation
        'Qph (nC/ph)',...   % charge per phase
        'GSA (um2)',...     % geometric surface area
        'Qinj (uC/cm2)',... % charge injection
        maxPotentialTitle,...       % max potential
        'Vdrive (V)',...    % driving voltage
        'Completed On',...% date and time
        'Status'};
    tableData = table(...               % table
        time_data,...                   % time
        voltage_data,...                % voltage transient
        current_data,...                % current stimulation
        chargePhase_strings,...         % Qph
        geomSurfaceArea_strings,...     % GSA
        chargeInj_strings,...           % Qinj
        maxPotential_strings,...        % Emc
        voltageDrive_strings,...        % Vdrive
        dateTime_strings,...            % DateTime
        status_strings,...
        'VariableNames',tableHeading);
else
    tableHeading = {...     % heading
        'Time (us)',...     % time
        'Voltage (V)',...   % voltage transient
        'Current (uA)',...  % current stimulation
        'Qph (nC/ph)',...   % charge per phase
        'Qinj (mC/cm2)',... % charge injection
        maxPotentialTitle,...       % max potential
        'Vdrive (V)',...    % driving voltage
        'Completed On',...% date and time
        'Status'};    % date and time
    tableData = table(...               % table
        time_data,...                   % time
        voltage_data,...                % voltage transient
        current_data,...                % current stimulation
        chargePhase_strings,...         % Qph
        chargeInj_strings,...           % Qinj
        maxPotential_strings,...        % Emc
        voltageDrive_strings,...        % Vdrive
        dateTime_strings,...            % DateTime
        status_strings,...
        'VariableNames',tableHeading);
end


% Making .mat file
filename_mat_char = sprintf('%s.mat',filename_char);  % filename for .mat file (char)
filename_mat = convertCharsToStrings(filename_mat_char);             	% filename string for .mat file (string)
fprintf('Saving %s...',filename_mat);  
filename_mat_save = fullfile(savePath,filename_mat);% save path for .mat file
save(filename_mat_save,'structVT');          % save .mat file
% fprintf('%s\n',filename_mat);                       % .mat file saved
fprintf('OK.\n');  

% Make filename for .csv file
filename_csv_char = sprintf('%s.csv',filename_char);  % filename for .csv file (char)
filename_csv = convertCharsToStrings(filename_csv_char);             	% filename string for .csv file (string)
filename_csv_save = fullfile(savePath,filename_csv);                    % save path for .csv file
    
% Save spreadsheet
fprintf('Saving %s...',filename_csv);	% .csv file saved
writetable(tableData,filename_csv_save);% save .csv file
fprintf('OK.\n');
    
% Save figure
if ishandle(vtPlot)
    filename_fig_char = sprintf('%s.tiff',filename_char);  % filename for .tif file (char)
    filename_fig = convertCharsToStrings(filename_fig_char);             	% filename string for .tif file (string)
    filename_tif = fullfile(savePath,filename_fig);
    fprintf('Saving %s...',filename_fig_char);           % .csv file saved
    print(vtPlot,'-dtiff',filename_tif,'-r300');
    fprintf('OK.\n');   % .tif file saved
end
% fprintf('All files saved.\n');% all files saved

end