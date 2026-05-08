function filename_tif = saveChannelData2(...
    Data,...
    channelNum,...
    isBroken,...
    filename,...
    savePath)
%% Constants
N_TO_MICRO = 1e6;
% ULTRA_AREA = 500;

%% Function
% fprintf('Saving files...\n');

% Starting filename
filename_char = sprintf('%s_CH%02d',... % charge per phase
    filename,channelNum);   
if isBroken
    filename_char = append(filename_char,'_BAD');
end
% Capture data of channel
time_data = Data.Time * N_TO_MICRO;              % time data array
voltage_data = Data.Voltage;            % voltage data array
current_data = Data.Current;          % current data array
amplitude_data = Data.Amplitude;      	% amplitude value
chargePhase_data = Data.ChargePhase;      	% charge per phase value
surfaceArea_data = Data.SurfaceArea;% geometric surface area
chargeInj_data = Data.ChargeInjection;           % charge injection value
maxPotential_data = Data.PotentialExcursion;   % maximum cathodal potential value
accessVoltage_data = Data.AccessVoltage;
voltageDrive_data = Data.DrivingVoltage;           % driving voltage value
dateTime_data = Data.DateTime;                   % date and time completed
status_data = Data.Status;

% Arrays for single values
blank_len = length(time_data);          % length of data
blank_array = strings(blank_len,1);	% array of blank space
amplitude_strings = blank_array;
amplitude_strings(1) = string(amplitude_data);     	% add amplitude to array
chargePhase_strings = blank_array;
chargePhase_strings(1) = string(chargePhase_data);     	% add charge per phase to array
surfaceArea_strings = blank_array;
surfaceArea_strings(1) = string(surfaceArea_data);  % add charge per phase to array
chargeInj_strings = blank_array;
chargeInj_strings(1) = string(chargeInj_data);          % add charge injection to array
maxPotential_strings = blank_array;
maxPotential_strings(1) = string(maxPotential_data);        % add max potential to array
accessVoltage_strings = blank_array;
accessVoltage_strings(1) = string(accessVoltage_data);        % add access voltage to array
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

tableHeading = {...     % heading
    'Time (us)',...     % time
    'Voltage (V)',...   % voltage transient
    'Current (uA)',...  % current stimulation
    'Amplitude (uA)',...
    'Qph (nC/ph)',...   % charge per phase
    'Area (um2)',...     % geometric surface area
    'Qinj (mC/cm2)',... % charge injection
    maxPotentialTitle,...       % max potential
    'Vacc (V)',...
    'Vdrive (V)',...    % driving voltage
    'Completed On',...% date and time
    'Status'};
tableData = table(...               % table
    time_data,...                   % time
    voltage_data,...                % voltage transient
    current_data,...                % current stimulation
    amplitude_strings,...
    chargePhase_strings,...         % Qph
    surfaceArea_strings,...     % GSA
    chargeInj_strings,...           % Qinj
    maxPotential_strings,...        % Emc
    accessVoltage_strings,...
    voltageDrive_strings,...        % Vdrive
    dateTime_strings,...            % DateTime
    status_strings,...
    'VariableNames',tableHeading);

% Making .mat file
filename_mat_char = sprintf('%s.mat',filename_char);  % filename for .mat file (char)
filename_mat = convertCharsToStrings(filename_mat_char);             	% filename string for .mat file (string)
fprintf('Saving "%s"...',filename_mat);  
filename_mat_save = fullfile(savePath,filename_mat);% save path for .mat file
save(filename_mat_save,'Data');          % save .mat file
% fprintf('%s\n',filename_mat);                       % .mat file saved
fprintf('OK.\n');  

% Make filename for .csv file
filename_csv_char = sprintf('%s.csv',filename_char);  % filename for .csv file (char)
filename_csv = convertCharsToStrings(filename_csv_char);             	% filename string for .csv file (string)
filename_csv_save = fullfile(savePath,filename_csv);                    % save path for .csv file
    
% Save spreadsheet
fprintf('Saving "%s"...',filename_csv);	% .csv file saved
writetable(tableData,filename_csv_save);% save .csv file
fprintf('OK.\n');
    
% Save figure
vtPlot = Data.Figure;
if ishandle(vtPlot)
    filename_fig_char = sprintf('%s.tiff',filename_char);  % filename for .tif file (char)
    filename_fig = convertCharsToStrings(filename_fig_char);             	% filename string for .tif file (string)
    filename_tif = fullfile(savePath,filename_fig);
    fprintf('Saving "%s"...',filename_fig_char);           % .csv file saved
    print(vtPlot,'-dtiff',filename_tif,'-r300');
    fprintf('OK.\n');   % .tif file saved
end
% fprintf('All files saved.\n');% all files saved

end